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
    """Thread UUIDs positively identifiable as live from the process table.

    This is a POSITIVE signal only. `codex resume <uuid>` puts the uuid in
    argv; a plain `codex` launch does not, because the id is created
    internally. So absence here means "could not tell", never "not running",
    and callers must not report a session dead on this basis.

    Our own process is excluded: this script's name contains "codex" and its
    argv carries the uuid whenever --thread is passed, so a naive scan matches
    itself and reports any named thread as live.
    """
    me = {str(os.getpid()), str(os.getppid())}
    try:
        ps = subprocess.run(["ps", "axo", "pid=,args="],
                            capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return set()
    out = set()
    for line in ps.splitlines():
        pid, _, args = line.strip().partition(" ")
        if pid in me:
            continue
        # The uuid must be the OPERAND of `resume`, not merely present in the
        # command line: `codex exec "look at <uuid>"` mentions one without
        # running it, and would otherwise mark that session live.
        m = re.search(r"(?:^|/)codex(?:\s+\S+)*?\s+resume\s+"
                      r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                      r"[0-9a-f]{4}-[0-9a-f]{12})\b", args)
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
    """Every rollout as (thread, path, cwd, mtime, live), newest first.

    `live` is True only when the process table positively confirms it; False
    means UNKNOWN, not dead. See running_threads.
    """
    live = running_threads()
    rows = []
    for f in glob.glob(os.path.join(CODEX_HOME, "sessions", "*", "*", "*",
                                    "rollout-*.jsonl")):
        m = re.search(r"rollout-.*?-([0-9a-f-]{36})\.jsonl$", f)
        if not m:
            continue
        try:
            mtime = os.path.getmtime(f)      # may vanish between glob and stat
        except OSError:
            continue
        t = m.group(1)
        rows.append((t, f, session_cwd(f), mtime, t in live))
    rows.sort(key=lambda r: r[3], reverse=True)
    return rows


def pick_session(thread=None, project=None, any_project=False, match=None):
    """Choose a session, and refuse rather than guess when it is ambiguous.

    Order of authority, deliberately: an explicit --thread wins outright, and
    is then CHECKED against --project rather than silently overriding it. A
    --match narrows the field but never overrules an explicit id.

    Returns (row, alternatives). `alternatives` lists other plausible
    candidates so callers can refuse; it is not merely the losing tier.
    """
    rows = all_sessions()
    if not rows:
        return None, []

    if project is not None and any_project:
        print("error: --project and --any-project contradict each other",
              file=sys.stderr)
        return None, []
    explicit_project = project is not None
    project_real = os.path.realpath(project or os.getcwd())

    def in_project(r):
        return r[2] and os.path.realpath(r[2]) == project_real

    # 1. An explicit id is the strongest statement of intent.
    if thread:
        hit = [r for r in rows if r[0] == thread]
        if not hit:
            print(f"warning: no rollout for thread {thread}", file=sys.stderr)
            return None, rows[:5]
        row = hit[0]
        if explicit_project and not in_project(row):
            # Do not quietly ignore one of two contradictory instructions.
            print(f"error: thread {thread} was started in {row[2]}, "
                  f"not {project_real}", file=sys.stderr)
            return None, []
        return row, []

    if match:
        rows = [r for r in rows if matches_text(r[0], match, r[2])]
        if not rows:
            print(f"no session matching {match!r}", file=sys.stderr)
            return None, []

    # 2. An explicit directory is a FILTER on every path, including list.
    if explicit_project:
        rows = [r for r in rows if in_project(r)]
        if not rows:
            print(f"no session started in {project_real}", file=sys.stderr)
            return None, all_sessions()[:5]
        confirmed = [r for r in rows if r[4]]
        tier = confirmed or rows
        # Alternatives are every OTHER session in this project, not just the
        # rest of the winning liveness tier: one confirmed and one unknown are
        # both plausible answers to "tell codex X".
        others = [r for r in rows if r[0] != tier[0][0]][:3]
        return tier[0], others

    # 3. Otherwise prefer, in order, and report the also-rans so a caller
    #    that mutates state can refuse.
    tiers = [] if any_project else [[r for r in rows if r[4] and in_project(r)]]
    tiers += [[r for r in rows if r[4]]]
    if not any_project:
        tiers += [[r for r in rows if in_project(r)]]
    tiers += [rows]

    for tier in tiers:
        if tier:
            # Alternatives are every OTHER recently-active session, not just
            # the rest of this tier — a running session and an idle one are
            # both plausible answers to "tell codex X".
            others = [r for r in rows if r[0] != tier[0][0]][:3]
            return tier[0], others
    return None, []


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


def tail_events(path, nbytes=4_000_000):
    """Parse the last chunk of a rollout without loading the whole file.

    Tolerates the file vanishing or rotating mid-read: Codex owns these files
    and may delete or migrate them while we look.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - nbytes)
            started_on_boundary = False
            if start > 0:
                fh.seek(start - 1)
                started_on_boundary = fh.read(1) == b"\n"
            fh.seek(start)
            chunk = fh.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return
    lines = chunk.splitlines()
    # The first line is suspect only when the seek landed mid-record. If the
    # byte before `start` is a newline, the chunk begins exactly on a record
    # boundary and that line is complete — dropping it would lose a real event.
    if start > 0 and not started_on_boundary:
        lines = lines[1:]
    for line in lines:
        try:
            d = json.loads(line)
        except Exception:
            continue
        if isinstance(d, dict):     # a bare scalar or array is not an event
            yield d


def summarize(path, n_msgs, n_cmds):
    """Messages, commands, rate-limit buckets and turn state from a rollout.

    Every field is defensive: these are Codex's private formats, so a schema
    change must degrade to less information rather than a traceback.
    """
    msgs, cmds, last_ts = [], [], None
    # Codex emits token_count under several `limit_id` buckets. "codex" carries
    # the real primary/secondary windows; "premium" carries only a credits blob
    # with both windows null, and it tends to arrive LAST — right when a window
    # is exhausted. Last-wins therefore blanks the limits exactly when they
    # matter. Keep the newest event per bucket instead.
    by_bucket, turn_done = {}, None
    for d in tail_events(path):
        last_ts = d.get("timestamp", last_ts)
        p = d.get("payload") or {}
        if not isinstance(p, dict):
            continue
        if p.get("type") in ("task_complete", "task_started"):
            turn_done = p["type"] == "task_complete"
        if p.get("type") == "token_count":
            r = p.get("rate_limits")
            if isinstance(r, dict):
                lid = r.get("limit_id")
                by_bucket[lid if isinstance(lid, str) else "?"] = r
        item = p.get("item") if isinstance(p.get("item"), dict) else None
        if not item:
            continue
        kind = item.get("type")
        if kind == "AgentMessage":
            txt = item.get("text") or ""
            if not txt:
                content = item.get("content")
                for c in (content if isinstance(content, (list, tuple)) else []):
                    if isinstance(c, dict) and isinstance(c.get("text"), str):
                        txt += c["text"]
            if not isinstance(txt, str):
                txt = str(txt)
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
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
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

    age = None
    if last_ts:
        try:
            t = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            if t.tzinfo is None:          # a naive stamp cannot be subtracted
                t = t.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - t).total_seconds()
        except Exception:
            age = None

    # State comes from the session's OWN events. The process table can only
    # confirm liveness, never deny it (see running_threads), so it is reported
    # as a separate line rather than folded into the verdict.
    if turn_done:
        state = "IDLE — turn finished, waiting at the prompt"
    elif age is not None and age > 900:
        state = "STALLED? — mid-turn but silent for %d min" % (age // 60)
    else:
        state = "WORKING — mid-turn"
    proc = "confirmed in process table" if running else "not confirmed (cannot tell)"

    print(f"thread : {thread}\nproject: {cwd or '?'}\n"
          f"state  : {state}\nprocess: {proc}\nlast   : {rel(last_ts)}  ({last_ts})")
    if alts:
        print(f"\nnote   : {len(alts)} other session(s) matched equally well — "
              f"pass --thread to be explicit:")
        for t, _f, c, _m, run in alts:
            print(f"         {t}  {'confirmed live' if run else 'liveness unknown'}  {c}")
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
            if not isinstance(rl, dict):
                continue
            for key, label in (("primary", "short window"), ("secondary", "weekly")):
                w = rl.get(key)
                if not isinstance(w, dict):
                    continue
                pct, resets = w.get("used_percent"), w.get("resets_at")
                try:
                    pct = float(pct)
                    when = datetime.fromtimestamp(float(resets),
                                                  tz=timezone.utc).astimezone()
                    when = f"{when:%a %H:%M %Z}"
                except (TypeError, ValueError, OSError, OverflowError):
                    # A renamed or retyped field must cost us this line, not
                    # the whole command.
                    continue
                flag = ("  <-- EXHAUSTED" if pct >= 99
                        else "  <-- nearly gone" if pct >= 90 else "")
                print(f"  [{lid}] {label:12} {pct:.0f}% used, resets {when}{flag}")
            c = rl.get("credits")
            if isinstance(c, dict) and not c.get("unlimited"):
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
    print(f"process         : {'confirmed live' if running else 'cannot tell'}")
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
    here = os.path.realpath(args.project or os.getcwd())
    if args.project is not None:
        # An explicitly named directory filters the listing. Previously it only
        # moved the `*` marker, so `list --project /nowhere` showed everything.
        rows = [r for r in rows if r[2] and os.path.realpath(r[2]) == here]
        if not rows:
            print(f"No Codex session was started in {here}")
            return 1
    if not rows:
        print("No Codex sessions found under", CODEX_HOME)
        return 1
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
        state = "live" if running else "?"
        print(f"{mark}{thread} {state:8} {age:>12}  {cwd or '?'}")
        if meta:
            label = meta.get("name") or (meta.get("preview") or "").replace("\n", " ")
            if label:
                print(f"{'':39}{'':8} {'':>12}  \u21b3 {label[:90]}")
        shown += 1
    print("\n* = started in this directory.  live = confirmed in the process "
          "table; ? = cannot tell.\nUse --thread <uuid> to target one.")
    return 0


def cmd_send(args):
    row, alts = pick_session(args.thread, args.project, args.any_project,
                             getattr(args, "match", None))
    if not row:
        print("No matching Codex session found. Pass --thread explicitly.")
        return 1
    thread, path, cwd, _mtime, running = row

    # Sending mutates another agent's work. Refuse whenever something else was
    # plausibly meant, unless the caller named the thread outright.
    if alts and not args.thread:
        print("Refusing to guess — more than one session could be meant:")
        print(f"  {thread}  {cwd}   <- would have been chosen")
        for t, _f, c, _m, run in alts:
            print(f"  {t}  {c}  ({'confirmed running' if run else 'liveness unknown'})")
        print("\nRe-run with --thread <uuid>, or --project <dir>.")
        return 1

    if not running and not args.force:
        print(f"Cannot confirm {thread} is running (Codex does not always put "
              f"the session id in its argv, so this is often unknowable).")
        print("Queue anyway with --force; the message waits until Codex reads it.")
        return 1

    print(f"target : {thread}\nproject: {cwd}")
    try:
        res = subprocess.run(
            # `--message=` (not `--message --`, which clap rejects) so a
            # message beginning with "-" is not parsed as an option. Verified
            # against codex 0.149.0.
            ["codex", "queue", "--thread", thread, f"--message={args.message}"],
            capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        print("error: `codex` is not on PATH — needed to queue a message.",
              file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("error: `codex queue` did not return within 60s; nothing was queued.",
              file=sys.stderr)
        return 1
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    if res.returncode == 0:
        print(f"\nQueued to {thread}. Delivery is asynchronous — Codex picks it up")
        print("when it next reads input. Confirm with: codex_session.py status")
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
