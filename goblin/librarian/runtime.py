"""The librarian while Goblin Launcher runs: per-turn context, tool traces, the shelf cache.

Agents that run outside our own loop (Grok Build, Claude Code, OpenClaw) reach this through hooks
and MCP over the service socket; room seats call it directly. Tool args and output from outside
agents are redacted before they are stored. Idle sessions close after two hours.
"""
from __future__ import annotations

import json
import secrets
import threading
import time

import os

from . import checker, interpret as interpret_mod, shelf as shelf_mod
from . import context as context_mod
from .store import Store
from .trace import Trace

SESSION_IDLE_S = 2 * 3600
ARGS_KEEP = 4000


def _safe(s) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(s or "agent"))


def _clip(v, n: int) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= n else s[:n] + "…"


class Session:
    def __init__(self, lib: "Librarian", agent: str, session: str):
        self.agent, self.session = agent, session
        self.job_id = time.strftime("%Y%m%d-%H%M%S-") + f"{_safe(agent)[:10]}-" + secrets.token_hex(2)
        self.trace = Trace(lib.cfg.path(lib.cfg["trace_dir"]) / f"{self.job_id}.jsonl", self.job_id)
        self.trace.meta.update({"agent": agent, "session": session, "external": True})
        self.last = time.time()
        self.context_calls = self.context_chars = 0
        self.pending = ""                 # context held for an agent that can't take it on prompt
        self.lock = threading.Lock()


class Librarian:
    def __init__(self, cfg, shelf_interval: int = 300):
        self.cfg = cfg
        self._pending_lock = threading.Lock()
        self._writing = threading.Lock()
        self.last_write = None
        self.shelf_interval = shelf_interval
        self._local = threading.local()
        self._shelf = {"checked": None, "passing": [], "rows": []}
        self._shelf_lock = threading.Lock()
        self.sessions: dict[tuple, Session] = {}
        self._sessions_lock = threading.Lock()
        self.recent: list[dict] = []
        self._stop = threading.Event()

    @property
    def store(self) -> Store:
        """sqlite connections can't cross threads: one per thread."""
        st = getattr(self._local, "store", None)
        if st is None:
            st = self._local.store = Store(self.cfg.path(self.cfg["store"]["path"]))
        return st

    # ---- shelf ------------------------------------------------------------------
    def check_shelf(self) -> dict:
        with self._shelf_lock:
            passing, failing, couldnt = shelf_mod.check_all(self.cfg)
            rows = ([{"id": e.get("id"), "fact": e.get("fact"), "state": "pass"} for e, _ in passing]
                    + [{"id": e.get("id"), "fact": e.get("fact"), "state": "fail", "detail": d} for e, d in failing]
                    + [{"id": e.get("id"), "fact": e.get("fact"), "state": "couldnt_run", "detail": d} for e, d in couldnt])
            self._shelf = {"checked": time.time(), "rows": rows,
                           "passing": [{"id": e.get("id"), "fact": e.get("fact")} for e, _ in passing]}
            return {"checked": self._shelf["checked"], "rows": rows}

    def shelf(self) -> dict:
        return {"checked": self._shelf["checked"], "rows": self._shelf["rows"]}

    def shelf_passing(self) -> list[dict]:
        return list(self._shelf["passing"])

    def shelf_checked_at(self):
        return self._shelf["checked"]

    def start(self) -> None:
        def loop():
            while not self._stop.is_set():
                try:
                    self.check_shelf()
                except Exception as e:  # noqa: BLE001 - keep refreshing
                    print(f"[librarian] shelf check failed: {e}", flush=True)
                self._stop.wait(self.shelf_interval)
                self._reap()
        threading.Thread(target=loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for s in list(self.sessions.values()):
            self._finish(s, "service_stopped")

    # ---- sessions -------------------------------------------------------------------
    def _session(self, agent, session, create=True) -> Session | None:
        key = (str(agent or "agent"), str(session or "default"))
        with self._sessions_lock:
            s = self.sessions.get(key)
            if s is None and create:
                s = self.sessions[key] = Session(self, *key)
            if s:
                s.last = time.time()
            return s

    def context_full(self, body: dict) -> dict:
        res = context_mod.assemble(self, body.get("prompt") or "", agent=body.get("agent"), kind=body.get("kind"),
                                   artifact=body.get("artifact"), error=body.get("error"),
                                   use_knowledge=body.get("knowledge", True))
        p = res["parts"]
        self.recent = (self.recent + [{
            "ts": time.time(), "agent": body.get("agent"), "session": body.get("session"), "chars": p.get("chars", 0),
            "ms": p.get("ms"), "deferred": bool(body.get("defer")), "shelf": p.get("shelf", {}).get("ids", []),
            "knowledge": (p.get("knowledge") or {}).get("sources", []) if (p.get("knowledge") or {}).get("used") else [],
        }])[-50:]
        if body.get("session"):
            s = self._session(body.get("agent"), body["session"])
            s.context_calls += 1
            s.context_chars += len(res["text"])
            if body.get("kind"):
                s.trace.meta.update({k: body.get(k) for k in ("kind", "artifact") if body.get(k)})
            if body.get("defer"):                # Grok drops prompt-time context: hold it for a tool result
                s.pending = res["text"]
                return {"deferred": True, "chars": len(res["text"]), "parts": res["parts"]}
        return res

    def trace_full(self, body: dict) -> dict:
        s = self._session(body.get("agent"), body.get("session"))
        args = checker.redact(_clip(body.get("args") or {}, ARGS_KEEP))
        out = checker.redact(str(body.get("output") or ""))
        with s.lock:
            rec = s.trace.call(str(body.get("tool") or "?"), {"redacted": args}, body.get("exit"), out,
                               family=f"ext:{_safe(s.agent)}")
            pending = ""
            if body.get("take_pending") and s.pending:
                pending, s.pending = s.pending, ""
        return {"ok": True, "job_id": s.job_id, "step": rec["step"], "pending": pending}

    def end_full(self, body: dict) -> dict:
        s = self._session(body.get("agent"), body.get("session"), create=False)
        return self._finish(s, body.get("status") or "unknown") if s else {"ok": False, "reason": "no such session"}

    def _finish(self, s: Session, status: str) -> dict:
        with self._sessions_lock:
            self.sessions.pop((s.agent, s.session), None)
        with s.lock:
            calls = s.trace.calls()
            s.trace.meta.update({"status": status, "context_calls": s.context_calls,
                                 "context_chars": s.context_chars, "ended": time.time()})
            if calls:
                s.trace.save()
        auto = None
        if calls and not s.trace.meta.get("kind"):
            # a terminal session: read its trace for a kind, a dead end and a verified finish (plain code)
            try:
                auto = interpret_mod.interpret(s.trace.records, self.cfg)
            except Exception as e:  # noqa: BLE001 - never lose the trace over this
                print(f"[librarian] interpreting {s.job_id} failed: {e}", flush=True)
            if auto:
                with s.lock:
                    s.trace.records = auto["records"]
                    s.trace.meta.update({"kind": auto["kind"], "key": auto["key"], "artifact": auto["artifact"],
                                         "tool_family": auto["family"], "auto_kind": auto["reason"]})
                    s.trace.save()
        if calls:
            self.store.log_job(job_id=s.job_id, kind=s.trace.meta.get("kind"), key=s.trace.meta.get("key"),
                               mode=self.cfg["mode"], holdout=0, injected_card=None, tokens_in=0, tokens_out=0,
                               steps=len(calls), status=status, verify_ok=None)
        if auto and self.cfg["mode"] in ("store", "live"):
            self._pending_add(str(s.trace.path), auto)
            if self.writer_loaded():
                threading.Thread(target=self.write_pending, daemon=True).start()
        return {"ok": True, "job_id": s.job_id, "steps": len(calls), "saved": bool(calls), "kind": (auto or {}).get("kind")}

    # ---- card candidates from terminal sessions --------------------------------------------
    # The writer is a model; on the GPU it is only loaded when the owner asks (the Ledger's button) or when
    # it already happens to be loaded. Until then candidates wait here.
    def _pending_path(self) -> str:
        return os.path.join(os.path.dirname(self.cfg.path(self.cfg["store"]["path"])), "pending-cards.json")

    def pending(self) -> list[dict]:
        try:
            with open(self._pending_path()) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return []

    def _pending_save(self, items: list[dict]) -> None:
        tmp = self._pending_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(items, f)
        os.replace(tmp, self._pending_path())

    def _pending_add(self, path: str, auto: dict) -> None:
        with self._pending_lock:
            items = [x for x in self.pending() if x["trace"] != path]
            items.append({"trace": path, "kind": auto["kind"], "key": auto["key"], "reason": auto["reason"], "queued": time.time()})
            self._pending_save(items)

    def writer_loaded(self) -> bool:
        """Is the writer model already loaded on the GPU (so writing loads nothing new)?"""
        try:
            name, mcfg = self.cfg.role_model("writer")
            goblin = getattr(self.cfg, "goblin", None)
            if not goblin or mcfg.get("provider") != (goblin.get("gpu") or {}).get("provider"):
                return False                          # not a GPU model: the button decides
            from .. import gpu
            p = gpu.provider(goblin)
            want = mcfg["model"].split(":")[0]
            return any(n.split(":")[0] == want for n in p.loaded())
        except Exception:  # noqa: BLE001
            return False

    def write_pending(self) -> list[dict]:
        """Write every queued candidate (one at a time). Returns each outcome."""
        from . import writer, providers
        if not self._writing.acquire(blocking=False):
            return []
        out = []
        try:
            for item in list(self.pending()):
                try:
                    t = Trace.load(item["trace"])
                    res = writer.write_job(t, self.cfg, self.store)
                except (OSError, providers.ProviderError, KeyError, ValueError) as e:
                    res = {"outcome": "writer_error", "reason": str(e)}
                out.append({**item, **res})
                with self._pending_lock:
                    self._pending_save([x for x in self.pending() if x["trace"] != item["trace"]])
            self.last_write = {"at": time.time(), "results": out}
            return out
        finally:
            self._writing.release()

    def _reap(self) -> None:
        cutoff = time.time() - SESSION_IDLE_S
        with self._sessions_lock:
            idle = [s for s in self.sessions.values() if s.last < cutoff]
        for s in idle:
            self._finish(s, "idle")

    # ---- the room's interface (goblin.agents.loop) ---------------------------------------
    def context(self, prompt: str, agent: str, session: str) -> str:
        return self.context_full({"prompt": prompt, "agent": agent, "session": session}).get("text") or ""

    def trace(self, agent, session, tool, args, exit_code, output) -> None:
        self.trace_full({"agent": agent, "session": session, "tool": tool, "args": args, "exit": exit_code,
                         "output": output})

    def end(self, agent, session, status) -> None:
        self.end_full({"agent": agent, "session": session, "status": status})

    def health(self) -> dict:
        rows = self._shelf["rows"]
        return {"mode": self.cfg["mode"], "shelf": {"pass": len(self._shelf["passing"]), "total": len(rows),
                                                    "checked": self._shelf["checked"]},
                "sessions": [{"agent": a, "session": s, "steps": len(x.trace.calls())}
                             for (a, s), x in list(self.sessions.items())],
                "recent_context": self.recent[-10:]}
