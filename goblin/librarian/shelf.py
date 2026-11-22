"""The map shelf: probed facts about your own setup (what runs where).

shelf.yaml:
  - id: photos-host
    fact: "The photo server runs in CT 105 on the Proxmox host"
    probe: {read: pct_status, args: ["105"], match: {regex: "status: running"}}

Only entries whose probe passes right now reach the talker. A failing entry
is withheld and reported. Entries can be proposed by the talker, but they
land only if their probe passes at that moment.
"""
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

from . import probes


def load(cfg):
    p = cfg.path(cfg["shelf"])
    if not p.exists():
        return []
    return yaml.safe_load(p.read_text()) or []


def save(cfg, entries):
    p = cfg.path(cfg["shelf"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(entries, sort_keys=False))
    tmp.replace(p)


def check_all(cfg):
    """Probe every entry. Probes run in parallel (blocked-network probes wait out timeouts);
    results keep shelf order."""
    entries = load(cfg)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda e: probes.run_probe(cfg, e["probe"]) if "probe" in e else (None, None, "no probe"),
            entries))
    passing, failing, couldnt = [], [], []
    for e, (outcome, _, out) in zip(entries, results):
        if outcome is None:
            couldnt.append((e, out))
            continue
        (passing if outcome == probes.PASS else failing if outcome == probes.FAIL else couldnt).append(
            (e, out[:200] if outcome != probes.PASS else ""))
    return passing, failing, couldnt


def render(passing):
    if not passing:
        return ""
    lines = "\n".join(f"- {e['fact']}" for e, _ in passing)
    return f"<verified_map>\nFacts about this setup whose probes passed just now:\n{lines}\n</verified_map>"


def propose(cfg, entry_id, fact, probe, source_job):
    err = probes.validate_probe(cfg, probe)
    if err:
        return False, f"rejected: {err}"
    outcome, _, out = probes.run_probe(cfg, probe)
    if outcome != probes.PASS:
        return False, f"rejected: probe {outcome}: {out[:200]}"
    entries = [e for e in load(cfg) if e.get("id") != entry_id]
    entries.append({"id": entry_id, "fact": fact, "probe": probe,
                    "added": time.strftime("%Y-%m-%d"), "source_job": source_job})
    save(cfg, entries)
    return True, "saved (probe passed)"
