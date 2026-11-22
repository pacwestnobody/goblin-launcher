"""SQLite store: cards, the shadow key log, write outcomes, job metrics.

Card states: readable | superseded. Deleted cards are removed outright.
At most one readable card per key, enforced here.
"""
import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL,
  card TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'readable',
  bytes INTEGER NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  last_hit REAL,
  created REAL NOT NULL,
  couldnt_run INTEGER NOT NULL DEFAULT 0,
  needs_reverify INTEGER NOT NULL DEFAULT 0,
  superseded_at REAL,
  source_job TEXT
);
CREATE INDEX IF NOT EXISTS cards_key ON cards(key, state);
CREATE TABLE IF NOT EXISTS key_log (
  job_id TEXT, key TEXT, kind TEXT, route_hash TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS writes (
  job_id TEXT, key TEXT, outcome TEXT, reason TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS events (
  ts REAL, key TEXT, card_id INTEGER, event TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, kind TEXT, key TEXT, mode TEXT, holdout INTEGER,
  injected_card INTEGER, tokens_in INTEGER, tokens_out INTEGER, steps INTEGER,
  status TEXT, verify_ok INTEGER, ts REAL
);
"""


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- cards ----------------------------------------------------------
    def readable(self, key):
        r = self.db.execute("SELECT * FROM cards WHERE key=? AND state='readable' "
                            "ORDER BY created DESC LIMIT 1", (key,)).fetchone()
        return self._row(r)

    def get(self, card_id):
        return self._row(self.db.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone())

    def _row(self, r):
        if r is None:
            return None
        d = dict(r)
        d["card"] = json.loads(d["card"])
        return d

    def put(self, key, card, source_job):
        """Insert a new readable card. Any older readable card for the key is superseded now."""
        now = time.time()
        body = json.dumps(card, sort_keys=True)
        with self.db:
            self.db.execute("UPDATE cards SET state='superseded', superseded_at=? "
                            "WHERE key=? AND state='readable'", (now, key))
            cur = self.db.execute("INSERT INTO cards(key, card, bytes, created, source_job) "
                                  "VALUES (?,?,?,?,?)", (key, body, len(body.encode()), now, source_job))
        self.event(key, cur.lastrowid, "written", source_job)
        return cur.lastrowid

    def hit(self, card_id):
        with self.db:
            self.db.execute("UPDATE cards SET hits=hits+1, last_hit=?, couldnt_run=0 WHERE id=?",
                            (time.time(), card_id))

    def refresh(self, card_id):
        with self.db:
            self.db.execute("UPDATE cards SET last_hit=?, couldnt_run=0, needs_reverify=0 WHERE id=?",
                            (time.time(), card_id))

    def couldnt_run(self, card_id, limit):
        with self.db:
            self.db.execute("UPDATE cards SET couldnt_run=couldnt_run+1 WHERE id=?", (card_id,))
        row = self.get(card_id)
        if row and row["couldnt_run"] >= limit:
            self.delete(card_id, f"couldnt_run x{row['couldnt_run']}")
            return True
        return False

    def flag_reverify(self, card_id):
        with self.db:
            self.db.execute("UPDATE cards SET needs_reverify=1 WHERE id=?", (card_id,))

    def delete(self, card_id, why):
        row = self.get(card_id)
        with self.db:
            self.db.execute("DELETE FROM cards WHERE id=?", (card_id,))
        if row:
            self.event(row["key"], card_id, "deleted", why)

    def all_readable(self):
        return [self._row(r) for r in self.db.execute(
            "SELECT * FROM cards WHERE state='readable' ORDER BY created")]

    def readable_bytes(self):
        return self.db.execute("SELECT COALESCE(SUM(bytes),0) FROM cards WHERE state='readable'").fetchone()[0]

    def purge(self, cap_bytes, superseded_delay_s):
        """Size only. Drops superseded cards past their delay, then the coldest readable cards."""
        now = time.time()
        dropped = []
        with self.db:
            for r in self.db.execute("SELECT id, key FROM cards WHERE state='superseded' AND superseded_at<?",
                                     (now - superseded_delay_s,)).fetchall():
                self.db.execute("DELETE FROM cards WHERE id=?", (r["id"],))
                dropped.append((r["id"], "superseded"))
        while self.readable_bytes() > cap_bytes:
            r = self.db.execute("SELECT id FROM cards WHERE state='readable' "
                                "ORDER BY (last_hit IS NOT NULL), last_hit, hits, created LIMIT 1").fetchone()
            if r is None:
                break
            self.delete(r["id"], "over cap")
            dropped.append((r["id"], "over cap"))
        return dropped

    # ---- logs -----------------------------------------------------------
    def event(self, key, card_id, event, detail=""):
        with self.db:
            self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                            (time.time(), key, card_id, event, str(detail)[:500]))

    def log_key(self, job_id, key, kind, route_hash):
        with self.db:
            self.db.execute("INSERT INTO key_log VALUES (?,?,?,?,?)",
                            (job_id, key, kind, route_hash, time.time()))

    def log_write(self, job_id, key, outcome, reason=""):
        with self.db:
            self.db.execute("INSERT INTO writes VALUES (?,?,?,?,?)",
                            (job_id, key, outcome, str(reason)[:500], time.time()))

    def log_job(self, **j):
        cols = ["job_id", "kind", "key", "mode", "holdout", "injected_card", "tokens_in",
                "tokens_out", "steps", "status", "verify_ok", "ts"]
        j.setdefault("ts", time.time())
        with self.db:
            self.db.execute(f"INSERT OR REPLACE INTO jobs({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                            [j.get(c) for c in cols])

    def kind_median_steps(self, kind, min_history):
        # Unassisted walks only: a job shortened by a card must not pull the median down,
        # or a working card would switch its own job kind off.
        rows = [r[0] for r in self.db.execute("SELECT steps FROM jobs WHERE kind=? AND steps IS NOT NULL "
                                              "AND injected_card IS NULL ORDER BY steps", (kind,))]
        if len(rows) < min_history:
            return None
        n = len(rows)
        return rows[n // 2] if n % 2 else (rows[n // 2 - 1] + rows[n // 2]) / 2
