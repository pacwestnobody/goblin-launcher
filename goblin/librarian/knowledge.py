"""Knowledge retrieval from the notes index (build it with `goblin librarian index`).

The service calls this itself on every turn; no model has to remember a script.
Reads the sqlite-vec index read-only. Embeddings come from Ollama (nomic-embed-text by default),
the same model the index was built with.

librarian.yaml:
  knowledge:
    db: ~/.local/share/goblin/knowledge.sqlite
    synonyms: {nas: [storage, mynas01]}   # your own words, added to the built-in ones
    embed_url: http://127.0.0.1:11434/api/embed
    model: nomic-embed-text
    top: 4
    max_distance: 1.25       # L2; lower = stricter
    min_best_score: 0.45     # best hit must reach this to count as "sufficient"
    max_lines: 40            # per chunk
"""
import sqlite3
from pathlib import Path

import json
import urllib.error
import urllib.request

DEFAULTS = {
    "db": "~/.local/share/goblin/knowledge.sqlite",
    "embed_url": "http://127.0.0.1:11434/api/embed",
    "model": "nomic-embed-text",
    "top": 4,
    "max_distance": 1.25,
    "min_best_score": 0.45,
    "max_lines": 40,
    "timeout": 30,
}

# Home-lab synonyms (generic). Add your own host and pool names in librarian.yaml knowledge.synonyms.
SYNONYMS = {
    "proxmox": ["PVE", "hypervisor"],
    "vm": ["virtual machine", "guest", "qemu"],
    "lxc": ["container", "ct"],
    "passthrough": ["PCI passthrough", "IOMMU", "VFIO", "hostpci", "GPU pass-through"],
    "restore": ["rebuild", "vzdump restore", "qmrestore"],
    "opnsense": ["firewall", "edge router", "gateway"],
    "nat": ["port forward", "DNAT", "SNAT", "outbound NAT"],
    "bridge": ["vmbr0", "vmbr1", "vmbr2", "LAN bridge", "WAN bridge"],
    "vlan": ["VLAN segmentation", "802.1Q", "tagged VLAN"],
    "dns": ["AdGuard", "DNS filter", ".53"],
    "truenas": ["storage pool", "ZFS", "NFS share", "SMB share"],
    "pool": ["data pool", "WWN passthrough disk"],
    "retro": ["Batocera", "gaming VM", "emulator"],
    "moonlight": ["game streaming", "Sunshine", "remote play"],
    "backup": ["rebuild set", "vzdump backup"],
    "boot order": ["startup sequence", "power loss recovery"],
}


class KnowledgeError(RuntimeError):
    pass


def settings(cfg):
    return {**DEFAULTS, **(cfg.get("knowledge") or {})}


def expand_query(query, synonyms=None):
    """Add synonyms for home-lab terms, both directions."""
    synonyms = synonyms or SYNONYMS
    words = {w.lower() for w in query.split()}
    extra = []
    for term, syns in synonyms.items():
        if term in words or any(term.startswith(w) and len(w) > 3 for w in words):
            extra.extend(syns)
    reverse = {syn.lower().split()[0]: term for term, syns in synonyms.items() for syn in syns}
    for w in words:
        if w in reverse and reverse[w] not in words:
            extra.extend(synonyms.get(reverse[w], []))
    return " ".join([query] + list(dict.fromkeys(extra)))


def embed(text, s):
    req = urllib.request.Request(s["embed_url"], method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"model": s["model"], "input": [text]}).encode())
    try:
        with urllib.request.urlopen(req, timeout=s["timeout"]) as r:
            return json.loads(r.read())["embeddings"][0]
    except urllib.error.HTTPError as e:
        raise KnowledgeError(f"embedding failed ({e.code}): {e.read()[:200]!r}") from e
    except (OSError, ValueError, KeyError) as e:
        raise KnowledgeError(f"embedding service unreachable: {e}") from e


def _connect(db):
    import sqlite_vec
    p = Path(db).expanduser()
    if not p.is_file():
        raise KnowledgeError(f"knowledge index not found: {p}")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def score_of(distance):
    return round(max(0.0, 1.0 - min(distance / 2.0, 1.0)), 3)


def search(cfg, query, top=None):
    """Return {"hits": [...], "sufficient": bool, "best": float, "expanded": str}.

    A hit is {"source", "score", "distance", "text"}. Closest first, near-duplicate sources
    collapsed to the best one."""
    import sqlite_vec
    s = settings(cfg)
    top = top or s["top"]
    syn = dict(SYNONYMS)
    for term, more in (s.get("synonyms") or {}).items():
        syn[term] = list(dict.fromkeys(syn.get(term, []) + list(more)))
    expanded = expand_query(query, syn)
    vec = embed(expanded, s)
    conn = _connect(s["db"])
    try:
        rows = conn.execute(
            "SELECT item_id, content, source_hint, distance FROM chunks "
            "WHERE embedding MATCH ? AND k = 10",
            (sqlite_vec.serialize_float32(vec),)).fetchall()
    finally:
        conn.close()
    rows = sorted((r for r in rows if r[3] <= s["max_distance"]), key=lambda r: r[3])
    hits, seen = [], set()
    for _id, content, hint, dist in rows:      # one hit per source, best first (librarian_query.py)
        if hint in seen:
            continue
        seen.add(hint)
        text = "\n".join(content.strip().splitlines()[: s["max_lines"]])
        hits.append({"source": hint, "score": score_of(dist), "distance": round(dist, 4), "text": text})
        if len(hits) >= top:
            break
    best = hits[0]["score"] if hits else 0.0
    return {"hits": hits, "best": best, "expanded": expanded,
            "sufficient": bool(hits) and best >= s["min_best_score"]}


def render(result, max_chars=4000, owner="the owner"):
    """Context block for a prompt. Empty when nothing relevant was found."""
    if not result or not result["hits"]:
        return ""
    parts, used = [], 0
    for h in result["hits"]:
        block = f"[{h['score']:.2f}] {h['source']}\n{h['text']}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    if not parts:
        return ""
    body = "\n---\n".join(parts)
    return (f"<knowledge>\nNotes from {owner}'s knowledge library that look relevant (closest first). "
            "They can be out of date; verified facts and live tool output win.\n"
            f"{body}\n</knowledge>")
