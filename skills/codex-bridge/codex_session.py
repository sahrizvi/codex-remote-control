#!/usr/bin/env python3
"""Read status from, and address, a running Codex CLI session.

Codex writes every session to a JSONL "rollout" file under
~/.codex/sessions/YYYY/MM/DD/. That file is appended continuously, so
reading its tail is an near-live view of what the agent is doing.

Reasoning blocks are encrypted and unreadable; agent messages, shell
commands, exit codes and rate limits are plaintext.
"""
import argparse, glob, json, os, re, sqlite3, subprocess, sys
from datetime import datetime, timezone

CODEX_HOME = os.path.expanduser(os.environ.get("CODEX_HOME", "~/.codex"))


def running_threads():
    """Thread UUIDs of live `codex` processes."""
    try:
        ps = subprocess.run(["ps", "axo", "args="],
                            capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return set()
    out = set()
    for line in ps.splitlines():
        if "codex" not in line or "grep" in line:
            continue
        m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", line)
        if m:
            out.add(m.group(1))
    return out


def session_cwd(path):
    """The project a session was started in, from its session_meta header."""
    try:
        with open(path, "r", errors="replace") as fh:
            head = json.loads(fh.readline())
        return (head.get("payload") or {}).get("cwd")
    except Exception:
        return None


def all_sessions():
    """Every rollout as (thread, path, cwd, mtime, running), newest first."""
    live = running_threads()
    rows = []
    for f in glob.glob(os.path.join(CODEX_HOME, "sessions", "*", "*", "*",
                                    "rollout-*.jsonl")):
        m = re.search(r"rollout-.*?-([0-9a-f-]{36})\.jsonl$", f)
        if not m:
            continue
        t = m.group(1)
        rows.append((t, f, session_cwd(f), os.path.getmtime(f), t in live))
    rows.sort(key=lambda r: r[3], reverse=True)
    return rows


def matches_text(thread, needle, cwd):
    """Case-insensitive search over a session's name, opening prompt and path."""
    n = needle.lower()
    if cwd and n in cwd.lower():
        return True
    meta = thread_meta(thread) or {}
    for field in ("name", "title", "first_user_message", "preview"):
        v = meta.get(field)
        if v and n in str(v).lower():
            return True
    return False


def pick_session(thread=None, project=None, any_project=False, match=None):
    """Choose a session, preferring: explicit id > running in this project >
    running anywhere > most recent in this project > most recent anywhere.

    Returns (row, alternatives). `alternatives` is non-empty when the choice
    was ambiguous, so the caller can say so instead of silently guessing.
    """
    rows = all_sessions()
    if not rows:
        return None, []

    if match:
        # A description narrows the field first; precedence then applies
        # within the matches, so "the review session" still prefers a
        # running one over a stale one.
        rows = [r for r in rows if matches_text(r[0], match, r[2])]
        if not rows:
            print(f"no session matching {match!r}", file=sys.stderr)
            return None, []
        if not project:
            any_project = True

    if thread:
        hit = [r for r in rows if r[0] == thread]
        if hit:
            return hit[0], []
        print(f"warning: no rollout for thread {thread}", file=sys.stderr)
        return None, rows[:5]

    explicit = project is not None
    project = os.path.realpath(project or os.getcwd())

    def in_project(r):
        return r[2] and os.path.realpath(r[2]) == project

    if explicit:
        # An explicitly named directory is a FILTER, not a preference. Falling
        # through to "running anywhere" would answer about a different project
        # than the one asked about — silently, and plausibly.
        scoped = [r for r in rows if in_project(r)]
        if not scoped:
            print(f"no session started in {project}", file=sys.stderr)
            return None, rows[:5]
        running = [r for r in scoped if r[4]]
        tier = running or scoped
        return tier[0], tier[1:4]

    tiers = [] if any_project else [
        [r for r in rows if r[4] and in_project(r)],   # running, here
    ]
    tiers += [[r for r in rows if r[4]]]               # running anywhere
    if not any_project:
        tiers += [[r for r in rows if in_project(r)]]  # recent, here
    tiers += [rows]                                    # anything

    for tier in tiers:
        if tier:
            # Ambiguous only if several candidates share the winning tier.
            return tier[0], tier[1:4]
    return None, []


def tail_events(path, nbytes=4_000_000):
    """Parse the last chunk of a rollout without loading the whole file."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(max(0, size - nbytes))
        chunk = fh.read().decode("utf-8", "replace")
    lines = chunk.splitlines()[1:]          # first line is likely partial
    for line in lines:
        try:
            yield json.loads(line)
        except Exception:
            continue


def summarize(path, n_msgs, n_cmds):
    msgs, cmds, last_ts = [], [], None
    # Codex emits token_count under several `limit_id` buckets. "codex" carries
    # the real primary/secondary windows; "premium" carries only a credits blob
    # with both windows null, and it tends to arrive LAST — right when a window
    # is exhausted. Last-wins therefore blanks the limits exactly when they
    # matter. Keep the newest event per bucket instead.
    by_bucket, turn_done = {}, None
    for d in tail_events(path):
        last_ts = d.get("timestamp", last_ts)
        p = d.get("payload", {}) or {}
        if p.get("type") in ("task_complete", "task_started"):
            turn_done = p["type"] == "task_complete"
        if p.get("type") == "token_count" and p.get("rate_limits"):
            r = p["rate_limits"]
            by_bucket[r.get("limit_id") or "?"] = r
        item = p.get("item") if isinstance(p.get("item"), dict) else None
        if not item:
            continue
        kind = item.get("type")
        if kind == "AgentMessage":
            txt = item.get("text") or ""
            if not txt:
                for c in item.get("content", []) or []:
                    if isinstance(c, dict) and c.get("text"):
                        txt += c["text"]
            if txt.strip():
                msgs.append((d.get("timestamp"), txt.strip()))
        elif kind in ("CommandExecution", "LocalShellCall"):
            cmd = item.get("command") or item.get("action") or ""
            if isinstance(cmd, list):
                cmd = " ".join(str(c) for c in cmd)
            cmds.append((d.get("timestamp"), str(cmd), item.get("exit_code")))
    return msgs[-n_msgs:], cmds[-n_cmds:], by_bucket, turn_done, last_ts


def rel(ts):
    if not ts:
        return "?"
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return ts
    secs = (datetime.now(timezone.utc) - t).total_seconds()
    if secs < 90:
        return f"{int(secs)}s ago"
    if secs < 5400:
        return f"{int(secs/60)}m ago"
    return f"{secs/3600:.1f}h ago"


def cmd_status(args):
    row, alts = pick_session(args.thread, args.project, args.any_project,
                             getattr(args, "match", None))
    if not row:
        print("No matching Codex session found under", CODEX_HOME)
        for t, _f, cwd, _m, run in alts:
            print(f"  {t}  {'running' if run else 'idle   '}  {cwd}")
        return 1
    thread, path, cwd, _mtime, running = row
    live = running_threads()
    msgs, cmds, buckets, turn_done, last_ts = summarize(path, args.messages, args.commands)

    if thread not in live:
        state = "NOT RUNNING (process gone)"
    elif turn_done:
        state = "IDLE — turn finished, waiting at the prompt"
    else:
        state = "WORKING — mid-turn"
    print(f"thread : {thread}\nproject: {cwd or '?'}\n"
          f"state  : {state}\nlast   : {rel(last_ts)}  ({last_ts})")
    if alts:
        print(f"\nnote   : {len(alts)} other session(s) matched equally well — "
              f"pass --thread to be explicit:")
        for t, _f, c, _m, run in alts:
            print(f"         {t}  {'running' if run else 'idle'}  {c}")
    print()

    if msgs:
        print("=== recent messages ===")
        for ts, m in msgs:
            body = m if args.full else (m[:700] + ("…" if len(m) > 700 else ""))
            print(f"\n[{rel(ts)}] {body}")
    if cmds:
        print("\n=== recent commands ===")
        width = 10_000 if args.full else 160
        for ts, c, code in cmds:
            c = " ".join(c.split())
            suffix = "" if code is None else f"  (exit {code})"
            print(f"  [{rel(ts)}] {c[:width]}{suffix}")
    if buckets:
        print("\n=== rate limits ===")
        for lid, rl in sorted(buckets.items()):
            for key, label in (("primary", "short window"), ("secondary", "weekly")):
                w = rl.get(key)
                if not w:
                    continue
                reset = datetime.fromtimestamp(w["resets_at"], tz=timezone.utc).astimezone()
                pct = w["used_percent"]
                flag = "  <-- EXHAUSTED" if pct >= 99 else ("  <-- nearly gone" if pct >= 90 else "")
                print(f"  [{lid}] {label:12} {pct:.0f}% used, resets {reset:%a %H:%M %Z}{flag}")
            c = rl.get("credits") or {}
            if c and not c.get("unlimited"):
                print(f"  [{lid}] credits      balance {c.get('balance')}, "
                      f"has_credits={c.get('has_credits')}")
            if rl.get("rate_limit_reached_type"):
                print(f"  [{lid}] REACHED      {rl['rate_limit_reached_type']}")
    return 0


def state_db(table="threads"):
    """Newest `state_<N>.sqlite` that actually contains `table`.

    The `_<N>` suffix is a STORE GENERATION, not a schema version: Codex runs
    51 sqlx migrations inside state_5 for incremental changes and only bumps
    N when it needs a fresh file. So the highest N is the live one.

    Sort numerically, not lexicographically — `sorted()` puts "state_10"
    before "state_5" and would silently read a stale generation the day
    Codex ships one. Then verify the table is present, so a future rename
    degrades to an older readable store instead of an exception.
    """
    cands = []
    for f in glob.glob(os.path.join(CODEX_HOME, "state_*.sqlite")):
        m = re.search(r"state_(\d+)\.sqlite$", f)
        if m:
            cands.append((int(m.group(1)), f))
    for _gen, f in sorted(cands, reverse=True):
        try:
            con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            hit = con.execute(
                "select 1 from sqlite_master where type='table' and name=?",
                (table,)).fetchone()
            con.close()
            if hit:
                return f
        except Exception:
            continue
    return None


def thread_meta(thread):
    """Stored metadata for a thread, straight out of Codex's own DB.

    Not derived, not summarised: `title` and `preview` are the user's opening
    prompt as Codex recorded it, `name` is a label the user may have assigned,
    and model/reasoning_effort are what the session is actually running with.
    """
    db = state_db()
    if not db:
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cur = con.execute("select * from threads where id = ?", (thread,))
        row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"warning: could not read {db}: {e}", file=sys.stderr)
        return None
    finally:
        try: con.close()
        except Exception: pass


def cmd_meta(args):
    row, _alts = pick_session(args.thread, args.project, args.any_project,
                              getattr(args, "match", None))
    if not row:
        print("No matching Codex session found.")
        return 1
    thread, path, cwd, _mtime, running = row
    meta = thread_meta(thread) or {}

    print(f"thread          : {thread}")
    print(f"project         : {cwd or '?'}")
    print(f"state           : {'running' if running else 'idle/finished'}")
    for key, label in (("name", "name"), ("model", "model"),
                       ("reasoning_effort", "reasoning effort"),
                       ("thread_source", "source"), ("agent_role", "agent role"),
                       ("agent_nickname", "agent nickname"),
                       ("memory_mode", "memory mode"),
                       ("history_mode", "history mode")):
        v = meta.get(key)
        if v not in (None, ""):
            print(f"{label:16}: {v}")
    if meta.get("is_pinned"):
        print(f"{'pinned':16}: yes")
    for key, label in (("created_at_ms", "created"), ("updated_at_ms", "updated")):
        v = meta.get(key)
        if v:
            t = datetime.fromtimestamp(v / 1000).astimezone()
            print(f"{label:16}: {t:%Y-%m-%d %H:%M %Z}")

    # git provenance lives in the rollout header, not the DB
    try:
        with open(path, "r", errors="replace") as fh:
            g = (json.loads(fh.readline()).get("payload") or {}).get("git") or {}
        if g:
            print(f"{'branch':16}: {g.get('branch')}")
            print(f"{'commit':16}: {str(g.get('commit_hash'))[:12]}")
    except Exception:
        pass

    opening = meta.get("title") or meta.get("first_user_message") or ""
    if opening:
        print(f"\n=== what this session was asked to do "
              f"(opening prompt, {len(opening)} chars) ===")
        print(opening if args.full else
              opening[:args.chars] + ("\n…" if len(opening) > args.chars else ""))
    else:
        print("\n(no stored opening prompt for this thread)")
    return 0


def cmd_list(args):
    """Every session, so a specific one can be named."""
    rows = all_sessions()
    if args.match:
        rows = [r for r in rows if matches_text(r[0], args.match, r[2])]
    if not rows:
        print("No Codex sessions found under", CODEX_HOME)
        return 1
    here = os.path.realpath(args.project or os.getcwd())
    shown = 0
    print(f"{'thread':38} {'state':8} {'last event':>12}  project")
    for thread, path, cwd, mtime, running in rows:
        meta = thread_meta(thread) if args.titles else None
        if args.running_only and not running:
            continue
        if shown >= args.limit:
            break
        mark = "*" if cwd and os.path.realpath(cwd) == here else " "
        age = rel(datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat())
        state = "running" if running else "idle"
        print(f"{mark}{thread} {state:8} {age:>12}  {cwd or '?'}")
        if meta:
            label = meta.get("name") or (meta.get("preview") or "").replace("\n", " ")
            if label:
                print(f"{'':39}{'':8} {'':>12}  \u21b3 {label[:90]}")
        shown += 1
    print("\n* = started in this directory. Use --thread <uuid> to target one.")
    return 0


def cmd_send(args):
    row, alts = pick_session(args.thread, args.project, args.any_project,
                             getattr(args, "match", None))
    if not row:
        print("No matching Codex session found. Pass --thread explicitly.")
        return 1
    thread, _path, cwd, _mtime, _running = row
    live = running_threads()
    if alts:
        print("Refusing to guess: several sessions matched equally well.")
        print(f"  {thread}  {cwd}   <- would have been chosen")
        for t, _f, c, _m, run in alts:
            print(f"  {t}  {c}  ({'running' if run else 'idle'})")
        print("\nRe-run with --thread <uuid>.")
        return 1
    print(f"target : {thread}\nproject: {cwd}")
    if thread not in live and not args.force:
        print(f"Thread {thread} is not running; a queued message would sit unread.")
        print("Re-run with --force to queue anyway.")
        return 1
    res = subprocess.run(
        ["codex", "queue", "--thread", thread, "--message", args.message],
        capture_output=True, text=True)
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    if res.returncode == 0:
        print(f"\nQueued to {thread}. Delivery is asynchronous — Codex picks it up when it")
        print("next reads input. Confirm with: codex_session.py status")
    return res.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="what the session is doing now")
    s.add_argument("--thread"); s.add_argument("--messages", type=int, default=3)
    s.add_argument("--project", help="match sessions started in this directory "
                                     "(default: cwd)")
    s.add_argument("--any-project", action="store_true",
                   help="do not prefer sessions from this project")
    s.add_argument("--match", help="find a session by words in its opening "
                                   "prompt, name, or path")
    s.add_argument("--commands", type=int, default=8)
    s.add_argument("--full", action="store_true", help="do not truncate messages")
    s.set_defaults(fn=cmd_status)

    l = sub.add_parser("list", help="every known session, so you can pick one")
    l.add_argument("--limit", type=int, default=20)
    l.add_argument("--running-only", action="store_true")
    l.add_argument("--project")
    l.add_argument("--match", help="filter by words in the opening prompt or path")
    l.add_argument("--titles", action="store_true",
                   help="show each session's opening prompt")
    l.set_defaults(fn=cmd_list)

    m = sub.add_parser("meta", help="stored metadata: what the session is about")
    m.add_argument("--thread"); m.add_argument("--project")
    m.add_argument("--any-project", action="store_true")
    m.add_argument("--match", help="find a session by words in its opening prompt")
    m.add_argument("--chars", type=int, default=1200)
    m.add_argument("--full", action="store_true")
    m.set_defaults(fn=cmd_meta)

    q = sub.add_parser("send", help="queue an instruction into the session")
    q.add_argument("message"); q.add_argument("--thread")
    q.add_argument("--project"); q.add_argument("--any-project", action="store_true")
    q.add_argument("--match", help="find the target session by description")
    q.add_argument("--force", action="store_true")
    q.set_defaults(fn=cmd_send)

    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
