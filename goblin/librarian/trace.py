"""Tool-call traces. The writer works from these and nothing else.

One JSONL file per job. Record kinds:
  call    a tool call: step, tool, args, read_id/read_args if it was a read, exit, output
  fork    abandon_fork: at_step, reason
  finish  finish: status, evidence_step
  rerun   end-of-job rerun of a killing read, in the post-success state
  meta    first line: job kind, artifact, key, etc. (written at close)
The model's prose is never recorded here.
"""
import json
import time

from .keys import normalize, output_hash

OUTPUT_KEEP = 65536


class Trace:
    def __init__(self, path, job_id):
        self.path = path
        self.job_id = job_id
        self.records = []
        self.meta = {"job_id": job_id, "started": time.time()}

    def _add(self, rec):
        rec["ts"] = time.time()
        self.records.append(rec)
        return rec

    def next_step(self):
        return 1 + sum(1 for r in self.records if r["kind"] in ("call", "fork", "finish"))

    def call(self, tool, args, code, out, read_id=None, read_args=None, family="other"):
        out = out or ""
        return self._add({
            "kind": "call", "step": self.next_step(), "tool": tool, "family": family,
            "args": args, "read_id": read_id, "read_args": read_args,
            "exit": code, "output": out[:OUTPUT_KEEP], "output_norm": normalize(out, 4000),
            "output_hash": output_hash(out),
        })

    def fork(self, at_step, reason):
        return self._add({"kind": "fork", "step": self.next_step(), "at_step": at_step, "reason": reason})

    def finish(self, status, evidence_step, summary):
        return self._add({"kind": "finish", "step": self.next_step(), "status": status,
                          "evidence_step": evidence_step, "summary_len": len(summary or "")})

    def rerun(self, of_step, read_id, read_args, code, out):
        out = out or ""
        return self._add({"kind": "rerun", "of_step": of_step, "read_id": read_id, "read_args": read_args,
                          "exit": code, "output": out[:OUTPUT_KEEP],
                          "output_norm": normalize(out, 4000), "output_hash": output_hash(out)})

    # ---- queries ----------------------------------------------------------
    def calls(self):
        return [r for r in self.records if r["kind"] == "call"]

    def by_step(self, step):
        for r in self.records:
            if r.get("step") == step and r["kind"] == "call":
                return r
        return None

    def first_error(self):
        for r in self.calls():
            if r["exit"] not in (0, None):
                return r
        return None

    def save(self):
        # the job's kind is kept as job_kind on the meta line: "kind" marks what the line is
        meta = {k: v for k, v in self.meta.items() if k != "kind"}
        with open(self.path, "w") as f:
            f.write(json.dumps({**meta, "job_kind": self.meta.get("kind"), "kind": "meta"}) + "\n")
            for r in self.records:
                f.write(json.dumps(r) + "\n")

    @classmethod
    def load(cls, path):
        with open(path) as f:
            lines = [json.loads(ln) for ln in f if ln.strip()]
        first = lines[0] if lines else {}
        records = ("call", "fork", "finish", "rerun")
        # older files wrote the job kind over the "meta" marker; a first line with a job id that isn't a
        # record is the meta line either way
        is_meta = first.get("kind") == "meta" or (first.get("kind") not in records and "job_id" in first)
        meta = dict(first) if is_meta else {}
        t = cls(path, meta.get("job_id", ""))
        if "job_kind" in meta:
            meta["kind"] = meta.pop("job_kind")
        elif meta.get("kind") == "meta":
            meta.pop("kind")
        t.meta = meta
        t.records = lines[1:] if is_meta else lines
        return t
