"""`goblin librarian ...`: Path Librarian's own commands.

  add-model                         register a model for the talker/writer (key -> secrets.yaml, mode 600)
  models                            list models and role assignments
  mode shadow|store|live            switch the rollout stage
  run "task" --kind K --artifact A [--error-file F] [--verify CMD] [--yes]
  write TRACE.jsonl                 re-run the writer on a saved trace
  cards | show ID | delete ID | purge | reprobe
  shelf                             check every shelf fact now
  tools                             list the tools the talker will see
  report                            tokens, correctness, false hits, store size, shadow metrics
  index                             (re)build the knowledge index from your notes folder
"""
import argparse
import getpass
import json
import sys
import time
from pathlib import Path

import yaml

from . import config, probes, shelf, writer
from .store import Store
from .trace import Trace


def _cfg(args):
    return config.load(Path(args.home) if args.home else None)


def _settings_file(cfg):
    names = ("librarian.yaml", "config.yaml") if cfg.settings == cfg.root else ("librarian.yaml",)
    return next((cfg.settings / n for n in names if (cfg.settings / n).exists()), cfg.settings / "librarian.yaml")


def cmd_add_model(args):
    cfg = _cfg(args)
    ask = lambda label, cur, default="": cur or input(f"{label}{f' [{default}]' if default else ''}: ").strip() or default
    name = ask("name (e.g. qwen-local, grok, claude)", args.name)
    prov = ask("provider: openai (any OpenAI-compatible API) | anthropic", args.provider, "openai")
    defaults = {"openai": "http://localhost:11434/v1", "anthropic": "https://api.anthropic.com"}
    base = ask("base url", args.base_url, defaults.get(prov, ""))
    model = ask("model id", args.model)
    key = args.api_key
    if key is None and not args.no_key:
        key = getpass.getpass("API key (blank for none): ").strip() or None
    role = args.role if args.role is not None else (input("roles to assign (talker,writer or blank): ").strip() or None)
    config.add_model(cfg, name, prov, base, model, key, role=role)
    print(f"saved model '{name}'" + (" (key in secrets.yaml, chmod 600)" if key else ""))


def cmd_models(args):
    cfg = _cfg(args)
    for n, m in cfg["models"].items():
        has_key = "key" if (m.get("key_ref") or m.get("api_key_env")) else "no key"
        print(f"{n:16} {m.get('provider'):10} {m.get('model')}  {m.get('base_url', '')}  ({has_key})")
    print("roles:", json.dumps(cfg["roles"]))


def cmd_mode(args):
    p = _settings_file(_cfg(args))
    data = (yaml.safe_load(p.read_text()) if p.exists() else {}) or {}
    data["mode"] = args.mode
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    print(f"mode = {args.mode}")


def _print_event(e):
    ev = e.get("event")
    if ev == "assistant":
        print(f"\n{e['text']}")
    elif ev == "tool":
        print(f"  [step {e['step']}] {e['name']} -> exit {e['exit']}", file=sys.stderr)
    elif ev == "librarian":
        print(f"  [librarian/{e['phase']}] {e['result']}", file=sys.stderr)
    elif ev == "shelf_stale":
        print(f"  [shelf] withheld stale facts: {', '.join(map(str, e['ids']))}", file=sys.stderr)


def cmd_run(args):
    from .agent import Session
    cfg = _cfg(args)
    error = Path(args.error_file).read_text() if args.error_file else args.error
    s = Session(cfg, talker=args.talker)
    try:
        r = s.run(args.task, kind=args.kind, artifact=args.artifact, error=error, verify=args.verify,
                  auto_yes=args.yes, on_event=_print_event, write=not args.no_write)
    finally:
        s.close()
    print("\n" + json.dumps({k: v for k, v in r.items() if k != "summary"}, indent=2, default=str))


def cmd_write(args):
    cfg = _cfg(args)
    t = Trace.load(args.trace)
    print(json.dumps(writer.write_job(t, cfg, Store(cfg.path(cfg["store"]["path"]))), indent=2))


def cmd_cards(args):
    cfg = _cfg(args)
    st = Store(cfg.path(cfg["store"]["path"]))
    for r in st.all_readable():
        flag = " REVERIFY" if r["needs_reverify"] else ""
        print(f"#{r['id']:<4} hits={r['hits']:<3} {r['bytes']}B  {r['key']}{flag}")
        print(f"       {r['card']['route'][0][:90]}")
    print(f"readable bytes: {st.readable_bytes()} / cap {cfg['store']['cap_bytes']}")


def cmd_show(args):
    cfg = _cfg(args)
    r = Store(cfg.path(cfg["store"]["path"])).get(args.id)
    print(json.dumps(r, indent=2, default=str) if r else "no such card")


def cmd_delete(args):
    cfg = _cfg(args)
    Store(cfg.path(cfg["store"]["path"])).delete(args.id, "deleted by user")
    print(f"deleted #{args.id}")


def cmd_purge(args):
    cfg = _cfg(args)
    d = Store(cfg.path(cfg["store"]["path"])).purge(cfg["store"]["cap_bytes"], cfg["store"]["superseded_delay_s"])
    print(f"dropped {len(d)} card(s)")


def cmd_reprobe(args):
    """Hot-set maintenance. Repair cards can't be re-proven offline (their success check
    only means something right after a fix), so they are verified on use. Here we drop
    cards whose probes no longer validate against the current allowlist, and check the shelf."""
    cfg = _cfg(args)
    st = Store(cfg.path(cfg["store"]["path"]))
    for r in st.all_readable():
        for name, p in r["card"]["probes"].items():
            err = probes.validate_probe(cfg, p)
            if err:
                st.delete(r["id"], f"probe {name} invalid now: {err}")
                print(f"deleted #{r['id']}: {name}: {err}")
                break
    cmd_shelf(args)


def cmd_shelf(args):
    cfg = _cfg(args)
    passing, failing, couldnt = shelf.check_all(cfg)
    for e, _ in passing:
        print(f"PASS  {e['id']}: {e['fact']}")
    for e, why in failing:
        print(f"FAIL  {e['id']}: {e['fact']}  <- withheld ({why.strip()[:80]})")
    for e, why in couldnt:
        print(f"SKIP  {e.get('id')}: couldn't run ({str(why).strip()[:80]})")


def cmd_tools(args):
    from .tools import ToolRegistry
    cfg = _cfg(args)
    reg = ToolRegistry(cfg)
    for t in reg.tools.values():
        print(f"{t.name:34} {t.family:12} {'confirm' if t.confirm else ''}")
    reg.close()


def cmd_index(args):
    from .index import main as index_main
    index_main(_cfg(args))


def cmd_report(args):
    report(_cfg(args))


def report(cfg):
    st = Store(cfg.path(cfg["store"]["path"]))
    db = st.db
    print(f"mode: {cfg['mode']}   store: {st.readable_bytes()}B / {cfg['store']['cap_bytes']}B  "
          f"cards: {len(st.all_readable())}")
    print("\nby kind (holdout vs card injected vs other):")
    for kind, in db.execute("SELECT DISTINCT kind FROM jobs"):
        for label, where in (("holdout", "holdout=1"), ("injected", "holdout=0 AND injected_card IS NOT NULL"),
                             ("other", "holdout=0 AND injected_card IS NULL")):
            r = db.execute(f"SELECT COUNT(*), AVG(tokens_in+tokens_out), AVG(status='success'), "
                           f"AVG(verify_ok) FROM jobs WHERE kind IS ? AND {where}", (kind,)).fetchone()
            if r[0]:
                v = "-" if r[3] is None else f"{r[3]:.0%}"
                print(f"  {str(kind):18} {label:9} n={r[0]:<4} tokens={r[1]:.0f}  success={r[2]:.0%}  verify={v}")
    inj = db.execute("SELECT COUNT(*) FROM events WHERE event='injected'").fetchone()[0]
    col = db.execute("SELECT COUNT(*) FROM events WHERE event='collision'").fetchone()[0]
    bad = db.execute("SELECT COUNT(*) FROM events WHERE event='deleted' AND detail LIKE 'success probe failed%'").fetchone()[0]
    print(f"\ninjections={inj}  collisions={col}  cards killed by success check={bad}")
    if inj:
        print(f"false-hit rate after injection: {bad / inj:.1%}")
    since = time.time() - 30 * 86400
    keyed = db.execute("SELECT COUNT(*) FROM jobs WHERE key IS NOT NULL AND ts>?", (since,)).fetchone()[0]
    rep = db.execute("SELECT COALESCE(SUM(c-1),0) FROM (SELECT COUNT(*) c FROM jobs WHERE key IS NOT NULL "
                     "AND ts>? GROUP BY key)", (since,)).fetchone()[0]
    div = db.execute("SELECT COUNT(*) FROM (SELECT key FROM key_log GROUP BY key "
                     "HAVING COUNT(DISTINCT route_hash)>1)").fetchone()[0]
    print(f"\nshadow (30d): keyed jobs={keyed}  repeats={rep}"
          + (f" ({rep / keyed:.0%})" if keyed else "") + f"  keys with divergent routes={div}")
    print("\nwrite outcomes:")
    for o, n in db.execute("SELECT outcome, COUNT(*) FROM writes GROUP BY outcome ORDER BY 2 DESC"):
        print(f"  {o:16} {n}")
    for reason, n in db.execute("SELECT reason, COUNT(*) FROM writes WHERE outcome IN ('no_card','rejected') "
                                "GROUP BY reason ORDER BY 2 DESC LIMIT 6"):
        print(f"     {n:>4}  {reason[:90]}")
    if div:
        print("\nWARNING: one key maps to more than one route. The key is too coarse; fix it before going live.")



def main(argv=None):
    ap = argparse.ArgumentParser(prog="goblin librarian")
    ap.add_argument("--home", help="one self-contained folder instead of the goblin config/state dirs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-model")
    for f in ("name", "provider", "base-url", "model", "api-key", "role"):
        a.add_argument(f"--{f}")
    a.add_argument("--no-key", action="store_true")
    a.set_defaults(fn=cmd_add_model)
    sub.add_parser("models").set_defaults(fn=cmd_models)
    m = sub.add_parser("mode")
    m.add_argument("mode", choices=["shadow", "store", "live"])
    m.set_defaults(fn=cmd_mode)
    r = sub.add_parser("run")
    r.add_argument("task")
    for f in ("kind", "artifact", "error", "error-file", "talker"):
        r.add_argument(f"--{f}")
    r.add_argument("--verify", help="shell command on the builtin target; exit 0 = the job really worked")
    r.add_argument("--yes", action="store_true", help="skip confirmations")
    r.add_argument("--no-write", action="store_true")
    r.set_defaults(fn=cmd_run)
    w = sub.add_parser("write")
    w.add_argument("trace")
    w.set_defaults(fn=cmd_write)
    for name, fn in (("show", cmd_show), ("delete", cmd_delete)):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)
        p.set_defaults(fn=fn)
    for name, fn in (("cards", cmd_cards), ("purge", cmd_purge), ("reprobe", cmd_reprobe), ("shelf", cmd_shelf),
                     ("tools", cmd_tools), ("report", cmd_report), ("index", cmd_index)):
        sub.add_parser(name).set_defaults(fn=fn)
    args = ap.parse_args(argv)
    args.fn(args)
    return 0
