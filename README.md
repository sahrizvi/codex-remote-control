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

## Install

```bash
git clone https://github.com/sahrizvi/codex-bridge
mkdir -p ~/.claude/skills && cp -r codex-bridge/skills/codex-bridge ~/.claude/skills/
```

That makes it available in every project. For a single project, copy it to
`.claude/skills/` inside that repo instead.

Start a new Claude Code session — skills are discovered at startup — connect
Remote Control, and you are done.

## Then just ask

Everything is conversational. You never touch a command.

> **You:** what's codex doing?
>
> **Claude:** It's mid-turn in `~/code/your-project`, last event 23 seconds
> ago. Its latest message: *"The warning milestone is committed as
> `63591b8c`; clean compilation now passes with warnings-as-errors."* Last two
> commands both exited 0 — a compile and a commit. Its 5-hour window is at
> 81%, resetting at 16:12.

> **You:** is it stuck?
>
> **Claude:** No — idle rather than stuck. It finished its turn 6 minutes ago
> and is waiting at the prompt. Nothing is running.

> **You:** tell it to run the full suite before it commits anything else
>
> **Claude:** Queued to the session in `~/code/your-project`. It'll pick that
> up when it next reads input.

Other things worth asking:

- *which codex sessions are running?*
- *what was that session originally asked to do?*
- *what's the codex session in the review worktree doing?*
- *check on the architecture audit session*
- *is codex rate-limited?*

The last one matters more than it sounds. From outside the terminal,
**working**, **idle**, and **silently out of budget** look identical — and the
third is the one that quietly costs you an afternoon.

## Worth knowing before you rely on it

Remote control here is real but not symmetric:

- **Instructions are one-way.** They land in Codex's input queue. There's no
  delivery receipt — you confirm by asking for status again, or by watching
  the commits.
- **They arrive without context.** Codex sees a bare instruction with none of
  your Claude conversation, so keep them self-contained.
- **A rate-limited session swallows them.** The message waits, unread, until
  the window resets.
- **You see what Codex says and does, never what it is thinking.** Its
  reasoning is encrypted in the session log.
- **It refuses to guess.** If several sessions match, or the target isn't
  running, it says so rather than instructing the wrong agent.

<details>
<summary><b>Using it from a terminal, without Claude</b></summary>

The skill is a thin wrapper around one script, which works standalone:

```bash
python3 skills/codex-bridge/codex_session.py status   # what it's doing now
python3 skills/codex-bridge/codex_session.py list     # every session
python3 skills/codex-bridge/codex_session.py meta     # model, git, opening prompt
python3 skills/codex-bridge/codex_session.py send "…" # queue an instruction
```

Useful flags:

| | |
|---|---|
| `--thread <uuid>` | target a session by id |
| `--project <dir>` | target the session started in a directory |
| `--match <text>` | find one by words in its opening prompt |
| `--titles` | on `list`, show what each session was asked to do |
| `--running-only` | on `list`, hide finished sessions |
| `--full` | untruncated messages and commands |
| `--force` | on `send`, queue to a session that isn't running |

With none of `--thread`/`--project`/`--match`, it prefers a session **running
in the current directory**, then running anywhere, then most recent here, then
anything — and always prints which project it chose, so a wrong pick is
visible rather than silent. `--project` is a hard filter: if no session
started there it says so, rather than answering about a different project.

</details>

<details>
<summary><b>How it works</b></summary>

Nothing is inferred or summarised by the tool; it reads Codex's own artifacts.

- **Rollouts** — `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`,
  appended continuously. Only the tail is read, so a 140 MB session costs
  nothing to check.
- **Metadata** — `~/.codex/state_<N>.sqlite`, table `threads`: model,
  reasoning effort, timestamps, git branch and commit, and the opening prompt
  verbatim.
- **Sending** — `codex queue --thread <uuid> --message <text>`.

This skill opens no ports and adds no network surface of its own. The remote
half is entirely Claude Code's Remote Control.

Three details that were bugs before they were features, each found by testing
rather than reasoning:

**Rate limits arrive under several `limit_id` buckets.** `codex` carries the
real windows; `premium` carries only credits with both windows `null` — **and
it tends to arrive last, exactly when a window is exhausted.** Keeping the
most recent event blanks the limits at the moment they matter most. The newest
event *per bucket* is kept instead.

**`state_<N>.sqlite` is a store generation, not a schema version.** Sorting
those lexicographically picks `state_9` over `state_10`.

**`--project` used to be a preference** that fell through to "running
anywhere", so asking about a review worktree silently answered about the main
project whenever the worktree's session was idle.

</details>

## Requirements

Python 3.9+ (standard library only), and the `codex` CLI on `PATH` to send
instructions. Set `CODEX_HOME` if Codex is not at `~/.codex`.

## License

MIT
