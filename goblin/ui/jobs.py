"""Ledger jobs: one Path Librarian job at a time per request, streamed step by step.

A job runs on a target from librarian.yaml (the sandbox by default). Approvals:
  auto      never ask (default on the sandbox)
  readonly  ask only for shell/write that isn't a pure lookup (default elsewhere)
  ask       ask for every shell/write
"""
from __future__ import annotations

import copy
import secrets
import threading
import time
import traceback

from ..readonly import is_readonly

CONFIRM_TIMEOUT_S = 900
MAX_EVENTS = 5000


class Job:
    def __init__(self, req: dict):
        self.id = time.strftime("ui-%H%M%S-") + secrets.token_hex(2)
        self.req = req
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.state = "running"            # running | waiting_confirm | done | error
        self.pending: dict | None = None
        self.stop = False
        self.result: dict | None = None
        self.started = time.time()

    def push(self, e: dict) -> None:
        with self.cond:
            if len(self.events) < MAX_EVENTS:
                self.events.append({"i": len(self.events), "t": round(time.time() - self.started, 1), **e})
            self.cond.notify_all()

    def wait_events(self, after: int, timeout: float) -> list[dict]:
        with self.cond:
            if len(self.events) <= after and self.state in ("running", "waiting_confirm"):
                self.cond.wait(timeout)
            return self.events[after:]

    def summary(self) -> dict:
        return {"id": self.id, "state": self.state, "task": self.req.get("task", "")[:200],
                "target": self.req.get("target"), "talker": self.req.get("talker"), "kind": self.req.get("kind"),
                "started": self.started, "result": self.result,
                "pending": {"name": self.pending["name"], "args": self.pending["args"]} if self.pending else None}


class Jobs:
    def __init__(self, librarian_cfg_fn):
        self.cfg_fn = librarian_cfg_fn          # fresh librarian config per job
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def start(self, req: dict) -> Job:
        task = (req.get("task") or "").strip()
        if not task:
            raise ValueError("the task is empty")
        base = self.cfg_fn()
        target = req.get("target") or ("sandbox" if "sandbox" in base["targets"] else "local")
        if target not in base["targets"]:
            raise ValueError(f"unknown target {target}")
        talker = req.get("talker") or base["roles"].get("talker")
        if talker not in base["models"]:
            raise ValueError(f"unknown model {talker}")
        kind = req.get("kind") or None
        if kind and kind not in base["job_kinds"]:
            raise ValueError(f"unknown job kind {kind}")
        job = Job({**req, "task": task, "target": target, "talker": talker, "kind": kind})
        with self.lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, base), daemon=True).start()
        return job

    def _run(self, job: Job, base) -> None:
        from ..librarian.agent import Session
        from ..librarian.config import Config
        req = job.req
        data = copy.deepcopy(dict(base))
        data["builtin_tools"]["target"] = req["target"]
        policy = req.get("approve") if req.get("approve") in ("auto", "readonly", "ask") else \
            ("auto" if req["target"] == "sandbox" else "readonly")
        req["approve"] = policy
        if policy == "auto":
            data["builtin_tools"]["confirm"] = False
        cfg = Config(data, base.root, base.settings, base.goblin)
        artifact = (req.get("artifact") or "").strip() or None
        if artifact:
            artifact = f"{req['target']}:{artifact}"     # the same path on two targets is two places

        def confirm(name, args):
            if policy == "readonly" and name == "shell" and is_readonly(args.get("command")):
                job.push({"event": "auto_ok", "name": name})
                return True
            ev = threading.Event()
            job.pending = {"name": name, "args": args, "event": ev, "ok": False}
            job.state = "waiting_confirm"
            job.push({"event": "confirm", "name": name, "args": args})
            ev.wait(CONFIRM_TIMEOUT_S)
            ok = job.pending["ok"] if job.pending else False
            job.pending, job.state = None, "running"
            job.push({"event": "confirmed" if ok else "declined", "name": name})
            return ok

        about = (cfg["targets"].get(req["target"]) or {}).get("about", "")
        context = "\n".join(x for x in [f"<where_you_are>\nYour shell and file tools run on target "
                                        f"'{req['target']}'. {about}\n</where_you_are>", (cfg.get("guide") or "").strip()] if x)
        job.push({"event": "start", "target": req["target"], "talker": req["talker"], "approve": policy,
                  "mode": cfg["mode"], "kind": req.get("kind"), "artifact": artifact})
        sess = None
        try:
            sess = Session(cfg, talker=req["talker"])
            r = sess.run(req["task"], kind=req.get("kind"), artifact=artifact,
                         error=(req.get("error") or "").strip() or None, verify=(req.get("verify") or "").strip() or None,
                         on_event=job.push, confirm_cb=confirm, stop_cb=lambda: job.stop, context=context)
            job.result = {k: r.get(k) for k in ("job_id", "status", "steps", "tokens_in", "tokens_out", "injected_card",
                                                "card_verdict", "verify_ok", "write")}
            job.state = "done"
        except Exception as e:  # noqa: BLE001 - surface anything in the Ledger
            job.push({"event": "error", "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]})
            job.state = "error"
        finally:
            if sess:
                sess.close()
            with job.cond:
                job.cond.notify_all()

    def answer(self, job_id: str, ok: bool) -> bool:
        job = self.jobs.get(job_id)
        if not job or not job.pending:
            return False
        job.pending["ok"] = bool(ok)
        job.pending["event"].set()
        return True

    def stop(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        job.stop = True
        if job.pending:
            self.answer(job_id, False)
        return True
