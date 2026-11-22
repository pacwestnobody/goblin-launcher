"""`goblin librarian index`: build the knowledge index the librarian searches every turn.

Reads every .md / .txt file under the notes folder (config `paths.knowledge`), skipping hidden
folders (so `.backups/` never leaks old text in), splits Markdown on `## ` headers and long
sections into ~2,000-character pieces, embeds each piece with Ollama (librarian.yaml
`knowledge.embed_url` / `knowledge.model`, default nomic-embed-text) and writes a sqlite-vec
table to `knowledge.db`. Rebuilds from scratch each time; identical pieces are stored once.
Needs the `knowledge` extra: pip install -e ".[knowledge]".
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .knowledge import DEFAULTS

MAX_CHUNK = 2000


def _pieces(text: str, hint: str) -> list[tuple[str, str]]:
    if len(text) <= MAX_CHUNK:
        return [(text, hint)] if len(text) > 50 else []
    out, buf, size = [], [], 0
    for line in text.splitlines():
        if size + len(line) + 1 > MAX_CHUNK and buf:
            out.append("\n".join(buf).strip())
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        out.append("\n".join(buf).strip())
    return [(t, hint) for t in out if len(t) > 50]


def chunks(path: Path, root: Path) -> list[tuple[str, str]]:
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    if path.suffix == ".txt" and not any(l.startswith("## ") for l in lines):
        return [(text[i:i + MAX_CHUNK].strip(), f"{path.name} (block {i // MAX_CHUNK + 1})")
                for i in range(0, len(text), MAX_CHUNK) if len(text[i:i + MAX_CHUNK].strip()) > 50]
    sections, header, buf = [], None, []
    for line in lines:
        if line.startswith("## ") and not line.startswith("###"):
            if buf:
                sections.append(("\n".join(buf).strip(), f"{header or path.name} ({path.relative_to(root)})"))
            header, buf = line.lstrip("# ").strip(), [line]
        else:
            buf.append(line)
    if buf:
        sections.append(("\n".join(buf).strip(), f"{header or path.name} ({path.relative_to(root)})"))
    return [p for content, hint in sections for p in _pieces(content, hint)]


def files(root: Path) -> list[Path]:
    out = []
    for dp, dirs, fns in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        out += [Path(dp) / f for f in fns if f.endswith((".md", ".txt")) and not f.startswith(".")]
    return sorted(out)


def embed(text: str, url: str, model: str) -> list[float] | None:
    req = urllib.request.Request(url, method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"model": model, "input": [text]}).encode())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["embeddings"][0]
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return None                    # the model refused this piece; skip it
        raise


def build(root: Path, db: Path, embed_url: str, model: str) -> tuple[int, int]:
    import sqlite_vec
    found = files(root)
    print(f"{len(found)} files under {root}")
    seen, records = set(), []
    for f in found:
        for content, hint in chunks(f, root):
            h = hashlib.sha256(content.encode()).hexdigest()
            if h not in seen:
                seen.add(h)
                records.append((content, hint, str(f), h))
    if not records:
        raise SystemExit("nothing to index")
    dim = len(embed(records[0][0], embed_url, model) or [])
    if not dim:
        raise SystemExit("the embedding model returned nothing")
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute("DROP TABLE IF EXISTS chunks")
    conn.execute(f"CREATE VIRTUAL TABLE chunks USING vec0 (item_id INTEGER, content TEXT NOT NULL, source_hint TEXT NOT NULL,"
                 f" file_path TEXT NOT NULL, hash TEXT UNIQUE NOT NULL, embedding float32[{dim}])")
    done = 0
    for i, (content, hint, path, h) in enumerate(records):
        if i % 20 == 0:
            sys.stdout.write(f"\r  embedding {i}/{len(records)}")
            sys.stdout.flush()
        vec = embed(content, embed_url, model)
        if vec is None:
            continue
        done += 1
        conn.execute("INSERT INTO chunks (item_id, content, source_hint, file_path, hash, embedding) VALUES (?,?,?,?,?,?)",
                     (done, content, hint, path, h, sqlite_vec.serialize_float32(vec)))
    conn.commit()
    conn.close()
    print(f"\r{done}/{len(records)} pieces indexed into {db}")
    return done, len(records)


def main(cfg) -> int:
    k = {**DEFAULTS, **(cfg.get("knowledge") or {})}
    root = cfg.goblin.path("paths", "knowledge") if getattr(cfg, "goblin", None) else Path("~/knowledge").expanduser()
    if not root.is_dir():
        raise SystemExit(f"no notes folder at {root} (config paths.knowledge)")
    build(root, Path(k["db"]).expanduser(), k["embed_url"], k["model"])
    return 0
