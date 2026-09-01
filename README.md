# codex-bridge

A [Claude Code](https://claude.com/claude-code) skill for watching and
addressing running **Codex CLI** sessions.

Codex appends every session to a JSONL *rollout* file and keeps session
metadata in a local SQLite store. `codex-bridge` reads both, so you can ask
Claude *"what is Codex doing?"* and get a real answer instead of switching
terminals — and queue an instruction back without leaving the conversation.

```
$ codex_session.py status

thread : 01a024a7-c0b8-7310-8ff4-bc7404f76194
project: /Users/you/code/your-project
state  : WORKING — mid-turn
last   : 23s ago

=== recent messages ===
[1m ago] The warning milestone is committed as 63591b8c; clean compilation
        now passes with warnings-as-errors…

=== recent commands ===
  [2m ago] mix compile --force --warnings-as-errors  (exit 0)
  [1m ago] git commit -m "chore: clear compile warning backlog"  (exit 0)

=== rate limits ===
  [codex] short window 100% used, resets Tue 16:12 IST  <-- EXHAUSTED
  [codex] weekly        75% used, resets Mon 11:56 IST
```

## Why

A long-running Codex session is opaque from outside its terminal. You cannot
easily tell *working* from *idle at the prompt* from *silently out of
budget* — and the third looks exactly like the first two until you check.

## Install

**One project:**

```bash
mkdir -p .claude/skills
cp -r skills/codex-bridge .claude/skills/
```

**Everywhere:**

```bash
mkdir -p ~/.claude/skills
cp -r skills/codex-bridge ~/.claude/skills/
```

Start a new Claude Code session — skills are discovered at startup — then ask
*"what's codex doing?"*. The script also runs standalone:

```bash
python3 skills/codex-bridge/codex_session.py status
```

## Commands

| | |
|---|---|
| `status` | state, recent messages, commands with exit codes, rate limits |
| `list` | every session with state, age and project (`--titles`, `--running-only`) |
| `meta` | model, reasoning effort, git branch/commit, and the opening prompt |
| `send` | queue an instruction into a running session |

## Choosing a session

You usually have more than one — review worktrees and other repos leave
sessions behind. Sessions can be named three ways:

```bash
codex_session.py list                          # see them all
codex_session.py status --thread <uuid>        # by id
codex_session.py status --project /some/dir    # by working directory
codex_session.py status --match "architecture" # by words in its opening prompt
```

With none of those it prefers a session **running in the current directory**,
then running anywhere, then most recent here, then anything — and always
prints which project it chose. `--project` is a hard filter: if no session
started there, it says so rather than answering about a different one.

## Limits, stated plainly

- **Reasoning is encrypted.** You see what Codex says and does, never what it
  is thinking.
- **Sending is one-way and asynchronous.** `codex queue` puts a message in the
  input queue; there is no delivery receipt and no reply channel.
- **Queued messages arrive without context**, so write self-contained ones.
- **A rate-limited session will not bounce a message** — it sits unread until
  the window resets.

## How it works

- **Rollouts** — `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`.
  Only the tail is read, so a 140 MB file costs nothing.
- **Metadata** — `~/.codex/state_<N>.sqlite`, table `threads`. The `_<N>` is a
  store *generation*, not a schema version; the highest N is selected
  **numerically**, because a lexicographic sort picks `state_9` over
  `state_10`.
- **Rate limits** — Codex emits these under several `limit_id` buckets.
  `codex` carries the real windows; `premium` carries only credits with both
  windows null, **and it tends to arrive last, exactly when a window is
  exhausted**. Taking the most recent event therefore blanks the limits at the
  moment they matter, so the newest event *per bucket* is kept.

That last one was a real bug, found by testing rather than reasoning.

## Requirements

Python 3.9+, and the `codex` CLI on `PATH` for `send` (`status`, `list` and
`meta` read files directly). Set `CODEX_HOME` if Codex is not at `~/.codex`.

## License

MIT
