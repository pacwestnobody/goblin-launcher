"""Rooms on disk: one JSON file per room, written atomically (temp file, fsync, rename), so a
restart mid-save can't leave an empty room. An unreadable file is set aside, not fatal."""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Rooms:
    def __init__(self, folder: Path):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)

    def _file(self, rid: str) -> Path:
        if not rid or "/" in rid or "." in rid:
            raise KeyError(rid)
        return self.dir / f"{rid}.json"

    def _write(self, room: dict) -> None:
        path = self._file(room["id"])
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with open(tmp, "w") as f:
            f.write(json.dumps(room, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _room_files(self):
        """Room files only: <id>.json. Room ids never contain a dot; seats keep other JSON here."""
        return [p for p in self.dir.glob("*.json") if "." not in p.stem]

    def _load(self, rid: str) -> dict | None:
        if rid in self._cache:
            return self._cache[rid]
        path = self._file(rid)
        if not path.is_file():
            return None
        try:
            room = json.loads(path.read_text())
        except (OSError, ValueError):
            path.rename(path.with_name(f"{path.name}.unreadable-{int(time.time())}"))
            return None
        if not isinstance(room, dict) or not room.get("id"):
            return None
        self._cache[rid] = room
        return room

    def get(self, rid: str) -> dict | None:
        with self.lock:
            room = self._load(rid)
            return copy.deepcopy(room) if room else None

    def save(self, room: dict) -> None:
        with self.lock:
            self._cache[room["id"]] = room
            self._write(room)
            self.changed.notify_all()

    def create(self, *, brief: str, seats: list[str], mode: str = "sequential", rid: str | None = None,
               **extra) -> dict:
        room = {"id": rid or datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6], "created": now(),
                "brief": brief.strip(), "seats": seats, "mode": mode if mode in ("sequential", "parallel") else "sequential",
                "messages": [], "status": "idle", **extra}
        self.save(room)
        return copy.deepcopy(room)

    def append(self, rid: str, msg: dict) -> dict:
        with self.lock:
            room = self._load(rid)
            if room is None:
                raise KeyError(rid)
            msg.setdefault("id", uuid.uuid4().hex)
            msg.setdefault("ts", now())
            room.setdefault("messages", []).append(msg)
            room["updated"] = now()
            self.save(room)
            return copy.deepcopy(msg)

    def set_status(self, rid: str, status: str) -> None:
        with self.lock:
            room = self._load(rid)
            if room:
                room["status"] = status
                self.save(room)

    def listing(self, limit: int = 30) -> list[dict]:
        out = []
        for p in sorted(self._room_files(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            r = self.get(p.stem)
            if r:
                out.append({"id": r["id"], "created": r.get("created"), "seats": r.get("seats"),
                            "brief": (r.get("brief") or "")[:120], "messages": len(r.get("messages") or []),
                            "bridge": bool(r.get("bridge"))})
        return out

    def clear_stuck(self) -> list[str]:
        """A killed turn leaves 'thinking' behind, which disables Send. Reset at start."""
        fixed = []
        for p in self._room_files():
            r = self.get(p.stem)
            if r and r.get("status") == "thinking":
                self.set_status(r["id"], "idle")
                fixed.append(r["id"])
        return fixed
