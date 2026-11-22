"""The room: the owner and several AI seats in one conversation, with cross-talk.

A message from the owner runs the seats it names (in that order), or every seat when it names
none; `parallel` rooms run them at once (local models still take turns on the GPU). Each seat sees
the whole room: its own past replies as its own, everyone else's labelled "[Name said]:".

The phone bridge is a fixed room bots post into over HTTP (allowed authors in config
`bridge.authors`). Room seats never answer it; goblin.bridge_wake wakes CLI agents for it.
"""
from __future__ import annotations

import threading
import time
import traceback

from .. import gpu
from ..agents import cli
from ..agents.loop import run_turn
from . import seats as seats_mod
from .store import Rooms, now

TEXT_MAX = 32000
OWNER_COLOR = "#e94560"


class Room:
    def __init__(self, cfg, librarian=None):
        self.cfg = cfg
        self.librarian = librarian
        self.rooms = Rooms(cfg.state / "rooms")
        self.rooms.clear_stuck()
        self.bridge_id = (cfg.get("bridge") or {}).get("room_id") or "phone-bridge"
        self.ensure_bridge()

    # ---- prompts ---------------------------------------------------------------------
    def system_prompt(self, seat: dict, room: dict, seats: dict) -> str:
        c = self.cfg
        others = [seats[s]["label"] for s in room.get("seats", []) if s not in (seat["id"], "you") and s in seats]
        rc = c.get("room") or {}
        return "\n".join(filter(None, [
            "## Identity", seat.get("identity") or f"You are {seat['label']}.",
            f"Model: {seat.get('provider')} `{seat.get('model')}`.",
            f"You have real tools on {c.get('machine')} (shell, read/list/grep files, write, replace, "
            "librarian_lookup, lab_run). Research may take several tool calls; write the findings in the same turn.",
            "Verify file writes and long jobs with a follow-up check.",
            f"Lab facts come from the librarian (added here when relevant, or librarian_lookup); don't invent "
            f"addresses, VMIDs or bridges. Notes library: {c.path('paths', 'knowledge')}.",
            rc.get("lab_summary") or "",
            "", "## Room",
            f"You share a room with {c.get('owner')} and: {', '.join(others) or 'nobody else'}. Their messages are "
            "labelled. Cross-talk is welcome: answer them, build on their work, check it with tools.",
            "Be concise. Short questions get short answers.",
            "", "## Brief", room.get("brief") or "(none)"]))

    def conversation(self, seat: dict, room: dict, seats: dict) -> list[dict]:
        msgs = [{"role": "system", "content": self.system_prompt(seat, room, seats)}]
        for m in room.get("messages") or []:
            text = (m.get("text") or "").strip()
            if not text:
                continue
            if m.get("author_id") == "you":
                msgs.append({"role": "user", "content": text})
            elif m.get("author_id") == seat["id"]:
                msgs.append({"role": "assistant", "content": text})
            else:
                msgs.append({"role": "user", "content": f"[{m.get('author_label') or m.get('author_id')} said]:\n{text}"})
        return msgs

    # ---- rooms -----------------------------------------------------------------------
    def create(self, brief: str, seat_ids: list[str], mode: str = "sequential") -> dict:
        known = seats_mod.all_seats(self.cfg)
        chosen = list(dict.fromkeys(s for s in seat_ids if s in known))
        if not chosen:
            raise ValueError("pick at least one AI seat")
        return self.rooms.create(brief=brief, seats=["you"] + chosen, mode=mode)

    def say(self, rid: str, text: str) -> dict:
        room = self.rooms.get(rid)
        if room is None:
            raise KeyError(rid)
        text = (text or "").strip()
        if not text or len(text) > TEXT_MAX:
            raise ValueError("empty or too long")
        self.rooms.append(rid, {"role": "user", "author_id": "you", "author_label": self.cfg.get("owner") or "You",
                                "color": OWNER_COLOR, "text": text})
        if not room.get("bridge"):
            threading.Thread(target=self.run_turns, args=(rid, text), daemon=True).start()
        return self.rooms.get(rid)

    def run_turns(self, rid: str, text: str) -> None:
        try:
            room = self.rooms.get(rid)
            seats = seats_mod.all_seats(self.cfg)
            in_room = [s for s in room.get("seats", []) if s in seats]
            order = seats_mod.addressed(text, in_room, seats) or in_room
            self.rooms.set_status(rid, "thinking")
            if room.get("mode") == "parallel":
                ts = [threading.Thread(target=self.reply, args=(rid, seats[s])) for s in order]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
            else:
                for s in order:
                    self.reply(rid, seats[s])
            self.rooms.set_status(rid, "idle")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self.rooms.set_status(rid, "error")

    def reply(self, rid: str, seat: dict) -> None:
        ok, why = seats_mod.ready(self.cfg, seat)
        try:
            if not ok:
                text = f"⚠️ {seat['label']} isn't ready: {why}"
            else:
                room = self.rooms.get(rid)
                msgs = self.conversation(seat, room, seats_mod.all_seats(self.cfg))
                if seat.get("provider") in seats_mod.CLI:
                    text = cli.room_turn(self.cfg, seat, msgs, rid)
                elif seats_mod.is_local(self.cfg, seat):
                    with gpu.turn_lock:
                        gpu.make_exclusive(self.cfg, seat["model"])
                        text = run_turn(self.cfg, seat, msgs, self.librarian, rid)
                else:
                    text = run_turn(self.cfg, seat, msgs, self.librarian, rid)
        except Exception as e:  # noqa: BLE001 - a seat's failure is a message in the room
            text = f"⚠️ {seat['label']} ({seat.get('model')}): {e}"
        self.rooms.append(rid, {"role": "assistant", "author_id": seat["id"], "author_label": seat["label"],
                                "color": seat["color"], "model": seat.get("model"), "text": text})

    # ---- phone bridge --------------------------------------------------------------------
    def authors(self) -> dict:
        return (self.cfg.get("bridge") or {}).get("authors") or {}

    def ensure_bridge(self) -> dict:
        room = self.rooms.get(self.bridge_id)
        members = ["you", *self.authors()]
        if room is None:
            return self.rooms.create(rid=self.bridge_id, brief="Phone bridge: bots and agents post here over HTTP. "
                                     "Room seats don't answer; CLI agents are woken for it.", seats=members, bridge=True)
        if set(members) - set(room.get("seats") or []):
            room["seats"] = list(dict.fromkeys((room.get("seats") or []) + members))
            self.rooms.save(room)
        return room

    def bridge_post(self, author: str, text: str, nonce: str = "") -> dict:
        a = self.authors().get(author)
        if a is None:
            raise ValueError("author_id must be one of: " + ", ".join(sorted(self.authors())))
        text = (text or "").strip()
        if not text or len(text) > TEXT_MAX:
            raise ValueError("empty or too long")
        nonce = (nonce or "").strip()[:120]
        with self.rooms.lock:
            room = self.ensure_bridge()
            if nonce:
                for m in room.get("messages") or []:
                    if m.get("author_id") == author and m.get("client_nonce") == nonce:
                        return {"ok": True, "duplicate": True, "message": m, "count": len(room["messages"])}
            msg = {"role": "assistant", "author_id": author, "author_label": a.get("label", author),
                   "color": a.get("color", "#888888"), "text": text, "bridge": True,
                   **({"client_nonce": nonce} if nonce else {})}
            msg = self.rooms.append(self.bridge_id, msg)
            count = len(self.rooms.get(self.bridge_id)["messages"])
        return {"ok": True, "duplicate": False, "room_id": self.bridge_id, "message": msg, "count": count}

    def bridge_after(self, after_id: str = "") -> dict:
        room = self.ensure_bridge()
        msgs = room.get("messages") or []
        if not after_id:
            return room
        ids = [m.get("id") for m in msgs]
        resync = after_id not in ids
        new = msgs if resync else msgs[ids.index(after_id) + 1:]
        return {"id": room["id"], "after_id": after_id, "resync": resync, "messages": new, "count": len(msgs)}

    def bridge_wait(self, after_id: str, timeout_s: float = 25) -> dict:
        deadline = time.monotonic() + min(50.0, max(1.0, timeout_s))
        with self.rooms.changed:
            while True:
                view = self.bridge_after(after_id)
                left = deadline - time.monotonic()
                if view.get("messages") or left <= 0:
                    view["timed_out"] = not view.get("messages")
                    return view
                self.rooms.changed.wait(timeout=min(left, 5))

    def status(self) -> dict:
        return {"seats": seats_mod.public(self.cfg), "bridge": {
            "room_id": self.bridge_id, "authors": list(self.authors()),
            "read": "/api/bridge", "post": "/api/bridge/message", "wait": "/api/bridge/wait"}, "time": now()}
