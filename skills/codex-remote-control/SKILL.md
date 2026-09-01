---
name: codex-remote-control
description: "Inspect and address Codex CLI sessions from inside Claude Code. Use when the user asks what Codex is doing, whether it is stuck or idle, how far along it is, what its rate limits are, which Codex sessions exist or are running, what a session was asked to do, what model or reasoning effort it is using, or asks to tell/instruct/message a Codex session. Sessions can be found by uuid, by working directory, or by words in their opening prompt. Also use for 'check on codex', 'is codex still running', 'codex status', 'list codex sessions', 'what is the codex session in <dir> doing'."
trigger: /codex-remote-control
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/codex_session.py *)
---

# codex-remote-control

Watch and address running Codex CLI sessions.

**Why this exists:** Codex has no remote control. Claude Code does. Used from
a Remote Control session, this skill lets someone drive their Codex runs from
the Claude Code app on their phone — Codex inherits a capability it lacks by
being proxied through Claude.

That shapes how to use it. The person asking is often not at the machine, may
be on a phone, and wants a decision-shaped answer: is it working, is it stuck,
is it out of budget, what did it just do, should I redirect it. Lead with
that, not with raw output.

Codex appends every session to a JSONL **rollout** file under
`~/.codex/sessions/YYYY/MM/DD/` and keeps metadata in `~/.codex/state_<N>.sqlite`.
Reading the rollout tail is near-live: in testing its last entry was 16
seconds old. Sending goes the other way, through `codex queue`.

## Usage

```bash
# Use an absolute path — this breaks from a subdirectory otherwise.
S="${CLAUDE_SKILL_DIR}/codex_session.py"

python3 "$S" list                        # every session — find the one you want
python3 "$S" list --titles               # with each session's opening prompt
python3 "$S" list --running-only         # only live ones
python3 "$S" meta                        # what this session was asked to do
python3 "$S" status                      # what is it doing right now
python3 "$S" status --messages 5 --full  # untruncated messages AND commands
python3 "$S" status --commands 20        # longer command history
python3 "$S" status --thread <uuid>      # a specific session
python3 "$S" send "your instruction"     # queue an instruction
```

Codex chains several `sed`/`rg` calls into one shell line, so the default
160-char command display cuts most entries mid-path. Use `--full` whenever
you need to know exactly which files it touched.

## Which session it picks

Every rollout's first line is a `session_meta` header carrying the **`cwd`**
the session was started in. That is what makes project scoping possible, and
it is the signal to trust — not process order.

Preference order, first non-empty tier wins:

1. explicit `--thread <uuid>`
2. **running, started in this project**
3. running anywhere
4. most recent, started in this project
5. most recent anywhere

Within a tier, newest by file mtime. The chosen session's `project` is always
printed, so a wrong pick is visible rather than silent.

**When a tier has several candidates it says so** and lists them rather than
guessing. `send` goes further and *refuses* — queueing an instruction into
the wrong agent's session is not a recoverable mistake.

### Asking about a session other than this project's

The preference order would otherwise trap you: with a session running in the
current directory, tiers 2 wins every time. Three ways out —

```bash
python3 "$S" list                          # find the uuid you want
python3 "$S" status --thread <uuid>        # target it explicitly
python3 "$S" status --project /some/dir    # scope to another directory
python3 "$S" status --match "architecture" # find it by description
python3 "$S" status --any-project          # ignore project preference entirely
```

### Naming a session in words

`--match` searches, case-insensitively, a session's assigned `name`, its
**opening prompt**, and its path. Codex stores the first user message as the
session's `title`, so the prompt is the natural handle — `name` is almost
always null unless someone set it deliberately.

Works on `status`, `meta`, `list` and `send`. Matching narrows the field
first; the usual precedence then applies among the matches, so
`--match review` still prefers a running review session to a stale one.

It is a substring search, so keep the phrase distinctive — a session whose
prompt says "this is not a code review" will match `review`. Use `list
--match <text> --titles` to see what you actually selected before acting.

`list` is the entry point: it prints every session with its state, age and
project, marking the current directory's with `*`. Start there whenever the
target is not obviously the local one.

**`--project <dir>` is a hard FILTER, not a preference.** If no session was
started there it errors and lists what does exist, rather than falling
through to a session elsewhere. That fallback was a real bug found by
demoing this: asking about a review worktree silently returned the MAIN
project's session, because the worktree's was idle and the main one running.
The default (no `--project`) still uses the preference tiers above.

An unknown `--thread` is an error listing the sessions that do exist, not a
silent fallback.

This matters more than it sounds: review worktrees, `codex review` runs and
other repos all leave sessions behind, so "the running codex session" is
routinely several.

## What you get back

- **state** — WORKING (mid-turn), IDLE (turn finished, waiting at the prompt),
  or NOT RUNNING. Derived from `task_complete` / `task_started` events, not
  just from the process list: a live process sitting at the prompt is not
  the same as one doing work, and that distinction is usually the question.
- **recent messages** — what Codex said, in its own words
- **recent commands** — every shell command with its exit code
- **rate limits** — short-window and weekly percentages, reset times, credit balance

`meta` reports Codex's own stored metadata rather than anything derived:
model, reasoning effort, memory mode, created/updated times, the git branch
and commit the session started on, and the opening prompt verbatim. It comes
from `~/.codex/state_<N>.sqlite`, table `threads`.

The `_<N>` is a store GENERATION, not a schema version — Codex runs dozens of
internal migrations within one file and bumps N only for a fresh store. The
script picks the highest N **numerically** (a lexicographic sort would choose
`state_9` over `state_10`) and verifies the table exists before using it.

Opening prompts run to thousands of characters, so `meta` truncates at
`--chars` (default 1200); `--full` prints the whole thing.

The rate limits are the most useful and least obvious part. Codex stalls
silently when a window is exhausted, and "idle" looks identical to
"out of budget" unless you check.

⚠️ Codex emits limits under several `limit_id` buckets. `codex` carries the
real short/weekly windows; `premium` carries only a credits blob with both
windows null — **and it tends to arrive last, right when a window is
exhausted.** Taking the most recent event therefore blanks the limits at
exactly the moment they matter. The script keeps the newest event per
bucket and prints the bucket name; a window at 100% is flagged EXHAUSTED.
This was a real bug found by testing, not a hypothetical.

## Four limits, state them when reporting

1. **Reasoning is encrypted.** `encrypted_content` blocks are unreadable.
   You see what Codex *says and does*, never what it is thinking.
2. **Sending is one-way and asynchronous.** `codex queue` puts a message in
   the input queue; Codex picks it up when it next reads input. No delivery
   receipt, no reply channel.
3. **Queued messages arrive without context.** Codex sees a bare instruction
   with none of the Claude conversation. Write self-contained messages.
4. **A stalled session will not bounce a message** — it sits unread until
   the rate-limit window resets.

## Guidance when using this skill

**Do not send without being asked.** Reading is free and safe; a queued
message is an instruction a running agent will act on, and it consumes
budget the session may be short of. Ask first unless the user's request was
explicitly "tell codex X".

`send` refuses when the target is not running, because a queued message to a
dead session sits unread while looking delivered — `--force` overrides. It
also refuses outright when several sessions matched equally well, rather
than picking one: instructing the wrong agent is not recoverable.

**Check rate limits before promising anything.** If the short window is
above ~80%, say so — the next instruction may not be picked up until reset.

**Report what it says, not a paraphrase.** Codex's own status lines are
usually more precise than a summary of them, and the whole point of the
bridge is getting its words rather than a guess.

**Verify claims against the repository.** A session's self-assessment is a
claim like any other. If it reports work complete, check `git log`, the
working tree, and the relevant code before repeating it as fact.

## Requirements

- `codex` CLI on `PATH` (only needed for `send`; `status` reads files directly)
- Python 3.7+
- Set `CODEX_HOME` if Codex is not at `~/.codex`
