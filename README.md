# codex-bridge

**Remote-control your Codex CLI sessions from your phone.**

Codex has no remote control. Start a long run and you are tethered to that
terminal — you cannot check on it from the sofa, and you certainly cannot
redirect it.

Claude Code *does* have remote control, and a phone app. `codex-bridge` is a
Claude Code skill that gives a Claude session eyes and hands on your Codex
sessions. Chain the two and Codex inherits the capability it does not have:

```
     phone                    your machine
┌──────────────┐        ┌──────────────────────┐
│  Claude Code │        │  Claude Code session │
│     app      │───────▶│          │           │
└──────────────┘ Remote │          ▼           │
                Control │    codex-bridge      │
                        │          │           │
                        │          ▼           │
                        │   Codex CLI session  │
                        │   (running, ~/repo)  │
                        └──────────────────────┘
```

From anywhere:

> **You:** what's codex doing?
>
> **Claude:** It's mid-turn in `~/code/your-project`, last event 23s ago. Its
> latest message: *"The warning milestone is committed as 63591b8c; clean
> compilation now passes with warnings-as-errors."* Last commands were
> `mix compile --force --warnings-as-errors` (exit 0) and a commit (exit 0).
> Its 5-hour window is at 81% — resets 16:12.

> **You:** tell it to stop after the current commit and push
>
> **Claude:** Queued to the session in `~/code/your-project`.

## The problem this actually solves

A long-running Codex session is opaque from outside its terminal, and the
three states you care about are indistinguishable at a glance:

- **working** — mid-turn, doing something
- **idle** — turn finished, waiting for you
- **out of budget** — rate-limited, silently stopped

The third looks exactly like the first two until you check. `codex-bridge`
answers all three, plus *what* it is doing and *whether it worked*, in one
question you can ask from a phone.

## Install

**One project:**

```bash
git clone https://github.com/sahrizvi/codex-bridge
mkdir -p .claude/skills && cp -r codex-bridge/skills/codex-bridge .claude/skills/
```

**Everywhere:**

```bash
mkdir -p ~/.claude/skills && cp -r codex-bridge/skills/codex-bridge ~/.claude/skills/
```

Start a new Claude Code session — skills are discovered at startup. Then
connect Remote Control and ask from your phone. The script also runs on its
own if you just want it in a terminal:

```bash
python3 skills/codex-bridge/codex_session.py status
```

## What you can ask for

| | |
|---|---|
| `status` | working / idle / rate-limited, recent messages, commands with exit codes, limits |
| `list` | every session with state, age and project |
| `meta` | model, reasoning effort, git branch and commit, and the prompt it was given |
| `send` | queue an instruction into a running session |

In conversation that is *"what's codex doing?"*, *"is it stuck?"*, *"which
codex sessions are running?"*, *"what was that session asked to do?"*,
*"tell codex to run the full suite before committing"*.

## Picking the right session

You will have more than one — review worktrees and other repos leave sessions
behind. Three ways to name one:

```bash
codex_session.py list                          # see them all
codex_session.py status --thread <uuid>        # by id
codex_session.py status --project /some/dir    # by working directory
codex_session.py status --match "architecture" # by words in its opening prompt
```

`--match` searches the session's opening prompt, which Codex stores as its
title — usually the most natural handle from a phone, where typing a UUID is
not happening.

With none of those it prefers a session **running in the current directory**,
then running anywhere, then most recent here, then anything — and always
prints which project it chose, so a wrong pick is visible rather than silent.
`--project` is a hard filter: if no session started there it says so, rather
than quietly answering about a different project.

## Limits, stated plainly

Remote control here is real but not symmetric. Know what you are getting:

- **Sending is one-way and asynchronous.** `codex queue` puts a message in
  Codex's input queue. There is no delivery receipt and no reply channel — you
  see the effect by asking for `status` again, or in the commits.
- **Queued messages arrive without context.** Codex sees a bare instruction
  with none of your Claude conversation. Write self-contained ones.
- **A rate-limited session will not bounce a message.** It sits unread until
  the window resets, which is the main reason `status` puts limits up front.
- **Reasoning is encrypted.** You see what Codex says and does, never what it
  is thinking. In practice its messages are verbose enough that this rarely
  bites.
- **`send` is deliberately conservative.** It refuses a session that is not
  running, and refuses outright when several matched equally well — telling
  the wrong agent to do something is not recoverable.

## How it works

Nothing is inferred or summarised by the tool; it reads Codex's own artifacts.

- **Rollouts** — `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`,
  appended continuously. Only the tail is read, so a 140 MB session costs
  nothing to check.
- **Metadata** — `~/.codex/state_<N>.sqlite`, table `threads`: model,
  reasoning effort, timestamps, and the opening prompt verbatim.
- **Sending** — `codex queue --thread <uuid> --message <text>`.

Three details that were bugs before they were features, each found by testing
rather than reasoning:

**Rate limits arrive under several `limit_id` buckets.** `codex` carries the
real windows; `premium` carries only credits with both windows `null` — **and
it tends to arrive last, exactly when a window is exhausted.** Keeping the most
recent event blanks the limits at the moment they matter most. The newest event
*per bucket* is kept instead.

**`state_<N>.sqlite` is a store generation, not a schema version.** Sorting
those lexicographically picks `state_9` over `state_10`.

**`--project` used to be a preference** that fell through to "running
anywhere", so asking about a review worktree silently answered about the main
project whenever the worktree's session was idle.

## Requirements

Python 3.9+ (standard library only), and the `codex` CLI on `PATH` for `send`
— `status`, `list` and `meta` read files directly. Set `CODEX_HOME` if Codex
is not at `~/.codex`.

Remote access is Claude Code's Remote Control and the Claude Code app; this
skill adds no network surface of its own and opens no ports.

## License

MIT
