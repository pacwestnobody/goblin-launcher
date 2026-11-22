"""Is a shell command read-only? Used to skip the approval prompt for pure lookups.

Conservative by design: anything not recognised is "not read-only" and gets asked.
A command passes only if it parses cleanly and every piece of every pipeline is a known
look-only program used in a look-only way. No redirection, no substitution, no sudo.
"""
import re
import shlex

# Programs that only observe, whatever their (non-flagged) arguments.
PLAIN = {
    "ls", "cat", "head", "tail", "less", "wc", "stat", "file", "du", "df", "free", "uptime", "ps",
    "pgrep", "whoami", "id", "groups", "uname", "which", "type", "whereis", "echo",
    "printf", "true", "false", "pwd", "lsblk", "lspci", "lsusb", "lscpu",
    "lsmod", "nproc", "rocminfo", "dmesg", "uptime", "w", "who",
    "last", "dig", "nslookup", "host", "ping", "traceroute", "tracepath", "mtr", "ss", "netstat",
    "grep", "egrep", "fgrep", "rg", "sort", "cut", "tr", "column", "nl", "rev", "fold", "jq",
    "yq", "md5sum", "sha256sum", "sha1sum", "basename", "dirname", "realpath", "readlink", "test", "[", "getent", "locale", "lsof", "vmstat", "iostat", "mpstat", "pidstat",
    "blkid", "findmnt", "seq", "sleep", "od",
    "strings", "netstat", "sockstat", "diff", "cmp", "comm", "join", "paste", "expand", "unexpand", "fmt", "pr",
}
# `env`/`printenv` print secrets: never auto.

# Programs that are read-only only with specific subcommands (first non-flag word).
SUBCOMMANDS = {
    "systemctl": {"status", "show", "cat", "list-units", "list-unit-files", "list-timers",
                  "list-sockets", "is-active", "is-enabled", "is-failed", "is-system-running"},
    "journalctl": None,                       # any args, but see FLAG_BANS
    "ip": {"addr", "address", "a", "link", "l", "route", "r", "neigh", "n", "rule", "maddr",
           "-s", "-4", "-6", "-br", "-brief", "-c", "-color", "-o", "-d", "-j", "-details", "-stats"},
    "docker": {"ps", "logs", "inspect", "images", "stats", "version", "info", "top", "port"},
    "podman": {"ps", "logs", "inspect", "images", "version", "info"},
    "git": {"status", "log", "diff", "show", "rev-parse", "describe", "ls-files", "blame", "shortlog"},
    "pip": {"show", "list", "freeze", "--version", "-V"},
    "pip3": {"show", "list", "freeze", "--version", "-V"},
    "ollama": {"list", "ps", "show", "--version", "-v"},
    "timedatectl": {"status", "show", "timesync-status", "show-timesync"},
    "zpool": {"status", "list", "iostat", "get", "history"},
    "zfs": {"list", "get"},
    "apt": {"list", "show", "policy", "search"},
    "apt-cache": {"show", "policy", "search", "depends", "rdepends"},
    "dpkg": {"-l", "-L", "-s", "--list", "--status", "--listfiles", "-S"},
    "python3": {"--version", "-V"},
    "python": {"--version", "-V"},
    "find": None,                             # see FLAG_BANS
    "sed": None,                              # only with -n and no w/e commands, see below
    "awk": None,                              # only without system()/getline/print >, see below
    "curl": None,                             # GET only, see below
    "mount": set(),                           # bare `mount` lists mounts
    "nft": {"list"},
    "iptables": {"-L", "-S", "--list", "--list-rules"},
    "iptables-save": None,
    "pveversion": None, "pct": {"list", "status", "config"}, "qm": {"list", "status", "config"},
    "pvesm": {"status", "list"},
    "pvesh": {"get", "ls"},
}

FLAG_BANS = {
    "find": {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls", "-fprint0"},
    "journalctl": {"--rotate", "--vacuum-size", "--vacuum-time", "--vacuum-files", "--flush",
                   "--sync", "--relinquish-var", "--smart-relinquish-var", "--setup-keys", "--update-catalog"},
    "curl": {"-X", "--request", "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F",
             "--form", "-T", "--upload-file", "-o", "--output", "-O", "--remote-name", "-K", "--config",
             "--json", "-c", "--cookie-jar"},
    "sort": {"-o", "--output"},
    "dmesg": {"-C", "--clear", "-c", "--read-clear", "-D", "--console-off", "-E", "--console-on", "-n"},
    "ping": {"-f"},
    "git": {"--output", "-o"},
}

# Shell syntax that can write, run hidden commands, or escalate. Checked on the raw string.
_BAD_SYNTAX = re.compile(r"(?<![<>&0-9])>|>>|<\(|>\(|\$\(|`|\bsudo\b|\bdoas\b|\bpkexec\b|\bsu\b|\beval\b|"
                         r"\bexec\b|\bsource\b|(^|[\s;|&])\.\s")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _sed_ok(args):
    if "-i" in args or any(a.startswith("-i") or a.startswith("--in-place") for a in args):
        return False
    script = " ".join(a for a in args if not a.startswith("-"))
    return not re.search(r"(^|[;}\s])[wWeE]\b|/[wWe]\s|/[gpI0-9]*[we](\s|$)", script)


def _awk_ok(args):
    prog = " ".join(args)
    return not re.search(r"\bsystem\s*\(|\bgetline\b|print[f]?[^;}]*>|\|\s*\"", prog)


# Read-only only in these exact shapes.
def _special(prog, args):
    non_flag = [a for a in args if not a.startswith("-")]
    if prog in ("hostname", "date"):
        return not non_flag and not any(a in ("-s", "--set", "-b", "--boot", "-F", "--file") or
                                        a.startswith("--set") for a in args)
    if prog == "git":   # skip global -C DIR / -c k=v, then check the subcommand
        rest = list(args)
        while rest and rest[0] in ("-C", "-c", "--git-dir", "--work-tree", "--no-pager"):
            rest = rest[1:] if rest[0] == "--no-pager" else rest[2:]
        return bool(rest) and rest[0] in SUBCOMMANDS["git"]
    if prog in ("timedatectl", "systemctl") and not non_flag:
        return not any(a in ("--now", "--force") for a in args)   # bare = show status / list units
    if prog == "hwclock":   # reading the RTC only; --set/--systohc/--hctosys/--adjust write
        reads = {"-r", "--show", "--get", "--verbose", "-v", "--utc", "-u", "--localtime", "-l",
                 "--debug", "-D"}
        return all(a in reads for a in args)
    if prog == "ifconfig":   # show only: bare, -a, or one interface name; never "eth0 down", addresses
        return all(a == "-a" for a in args if a.startswith("-")) and len(non_flag) <= 1
    if prog == "uniq":
        return len(non_flag) <= 1
    if prog == "tree":
        return "-o" not in args
    if prog == "ethtool":   # ethtool IFACE, or a show flag + IFACE
        shows = {"-S", "--statistics", "-i", "--driver", "-k", "--show-features", "-g", "--show-ring",
                 "-a", "--show-pause", "-c", "--show-coalesce", "-l", "--show-channels", "-m",
                 "--dump-module-eeprom", "-T", "--show-time-stamping", "--show-priv-flags", "--show-eee"}
        flags = [a for a in args if a.startswith("-")]
        return len(non_flag) == 1 and all(f in shows for f in flags)
    if prog == "chronyc":
        return bool(non_flag) and non_flag[0] in ("tracking", "sources", "sourcestats", "activity",
                                                   "ntpdata", "serverstats", "clients")
    if prog in ("nvidia-smi", "rocm-smi", "rocminfo", "sensors"):
        return not any(a.startswith(("--set", "--reset", "-r", "-pl", "-ac", "-rac", "-pm", "-e",
                                     "-c", "-s", "--gpu-reset", "--power-limit", "--persistence"))
                       and a not in ("-q", "-L", "--showuse", "--showtemp", "--showpower", "--showmeminfo")
                       for a in args)
    if prog == "smartctl":
        return all(a in ("-a", "-i", "-H", "-x", "-A", "-l", "--scan", "--all", "--info", "--health",
                         "error", "selftest") or not a.startswith("-") for a in args) and \
            not any(a in ("-s", "-t", "-X", "-o", "-S", "--test", "--smart", "--abort") for a in args)
    return None


def _segment_ok(words):
    while words and _ASSIGN.match(words[0]):
        return False                      # VAR=x cmd: env injection (LD_PRELOAD etc.): ask
    if not words:
        return True
    prog = words[0].rsplit("/", 1)[-1]
    args = words[1:]
    if prog in FLAG_BANS:
        bans = FLAG_BANS[prog]
        for a in args:
            if a in bans or any(a.startswith(b + "=") for b in bans if b.startswith("--")):
                return False
            if prog == "curl" and a.startswith("-") and not a.startswith("--") and len(a) > 2:
                # bundled short flags like -sSo: refuse if any banned letter is in there
                if any(f"-{ch}" in bans for ch in a[1:]):
                    return False
    sp = _special(prog, args)
    if sp is not None:
        return sp
    if prog == "sed":
        return _sed_ok(args)
    if prog == "awk" or prog == "gawk" or prog == "mawk":
        return _awk_ok(args)
    if prog in PLAIN:
        return True
    if prog in SUBCOMMANDS:
        allowed = SUBCOMMANDS[prog]
        if allowed is None:
            return True
        non_flag = [a for a in args if not a.startswith("-")]
        if prog == "ip":
            # ip [opts] OBJECT [show|list|...]: object must be readable, verb must be show/list
            objs = [a for a in args if a in allowed and not a.startswith("-")]
            if not objs:
                return False
            after = args[args.index(objs[0]) + 1:]
            verbs = [a for a in after if not a.startswith("-")]
            return not verbs or verbs[0] in ("show", "list", "lst", "sh", "ls", "get") or \
                verbs[0] not in ("add", "del", "delete", "set", "change", "replace", "flush", "append",
                                 "prepend", "save", "restore")
        if prog in ("dpkg", "iptables"):
            return any(a in allowed for a in args) and not any(
                a in ("-i", "--install", "-r", "--remove", "-P", "--purge", "-A", "-D", "-I", "-F",
                      "-X", "-N", "-R", "-Z") for a in args)
        if prog == "mount":
            return not args
        if not non_flag:
            return any(a in allowed for a in args)
        return non_flag[0] in allowed
    return False


def _pipeline_segments(cmd):
    """Quote-aware split on shell operators. Returns word lists, or None if the command
    uses an operator that could write or hide work (>, >>, (, ), lone &)."""
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    segs, cur = [], []
    try:
        for tok in lex:
            if tok in ("|", "||", "&&", ";", "\n"):
                segs.append(cur)
                cur = []
            elif tok == "<":
                cur.append(tok)            # input redirection only reads
            elif tok and set(tok) <= set("();<>|&"):
                return None
            else:
                cur.append(tok)
    except ValueError:
        return None
    segs.append(cur)
    out = []
    for seg in segs:
        words, skip = [], False
        for i, w in enumerate(seg):
            if skip:
                skip = False
                continue
            if w == "<":
                skip = True                # drop the filename too
                continue
            words.append(w)
        out.append(words)
    return out


def is_readonly(command):
    """True only when every part of the command is a recognised look-only use."""
    cmd = (command or "").strip()
    if not cmd or len(cmd) > 2000:
        return False
    # 2>&1 and 2>/dev/null are common in lookups and harmless; strip those exact forms first
    probe = re.sub(r"(^|\s)[12]?>&[12](?=\s|$|[;|&])", " ", cmd)
    probe = re.sub(r"(^|\s)[12]?>\s*/dev/null(?=\s|$|[;|&])", " ", probe)
    if _BAD_SYNTAX.search(probe):
        return False
    segs = _pipeline_segments(probe.replace("\n", " ; "))
    if segs is None:
        return False
    return all(_segment_ok(words) for words in segs)
