# codex-remote-control

**Remote-control your Codex CLI sessions from your phone.**

Codex has no remote control. Start a long run and you are tethered to that
terminal — you cannot check on it from the sofa, and you certainly cannot
redirect it.

Claude Code *does* have remote control, and a phone app. `codex-remote-control` is a
Claude Code skill that gives a Claude session eyes and hands on your Codex
sessions. Chain the two and Codex inherits the capability it does not have:

```
     phone                    your machine
┌──────────────┐        ┌──────────────────────┐
│  Claude Code │        │  Claude Code session │
│     app      │───────▶│          │           │
└──────────────┘ Remote │          ▼           │
                Control │    codex-remote-control      │
                        │          │           │
                        │          ▼           │
                        │   Codex CLI session  │
                        │   (running, ~/repo)  │
                        └──────────────────────┘
```

## Install

```bash
claude plugin marketplace add sahrizvi/codex-remote-control
claude plugin install codex-remote-control@codex-remote-control
```

Restart Claude Code, connect Remote Control, and you are done. No
configuration. Update later with `claude plugin update codex-remote-control`.

<details>
<summary>Manual install, if you would rather not add a marketplace</summary>

```bash
git clone https://github.com/sahrizvi/codex-remote-control /tmp/codex-remote-control
mkdir -p ~/.claude/skills
cp -R /tmp/codex-remote-control/skills/codex-remote-control ~/.claude/skills/
```

This adds exactly one directory — `~/.claude/skills/codex-remote-control`. Your other
skills are untouched; the only thing it can replace is an earlier copy of
itself. For a single project instead, copy it into that repo's
`.claude/skills/`.

</details>

## What you can do

All of it is conversation. You never type a command.

### See what a run is doing

> **You:** what's codex doing?
>
> **Claude:** Mid-turn in `~/code/your-project`, last event 23 seconds ago.
> Latest message: *"The warning milestone is committed as `63591b8c`; clean
> compilation now passes with warnings-as-errors."* Its last two commands both
> exited 0 — a compile and a commit.

You get its own words, the commands it ran, and whether they succeeded — not a
guess assembled from logs.

### Steer a run without going to your desk

> **You:** tell codex to run the full suite before committing anything else
>
> **Claude:** Queued to the session in `~/code/your-project`. It'll pick that
> up when it next reads input.

> **You:** tell it to stop after the current commit and push
>
> **Claude:** Queued.

Redirect a run that has gone the wrong way, add a constraint you forgot, or
tell it to wrap up — from a phone, mid-commute.

### Tell working apart from stuck apart from stalled

> **You:** is it stuck?
>
> **Claude:** Not stuck — idle. It finished its turn 6 minutes ago and is
> waiting at the prompt.

> **You:** is codex rate-limited?
>
> **Claude:** Yes. Its 5-hour window hit 100% about 20 minutes ago, mid-task.
> It resets at 16:12. Zero credits to fall back on, so it's stopped until then.

From outside the terminal, **working**, **idle**, and **silently out of
budget** look identical. The third is the one that quietly costs you an
afternoon, and it is the reason this exists.

### Keep track of several at once

Review worktrees and other repos leave sessions behind, so you usually have
more than one.

> **You:** which codex sessions are running?
>
> **Claude:** Two. One in `~/code/your-project`, running, active 1 minute ago.
> One in `/tmp/review-worktree-1182`, idle for two hours. Three older ones are
> finished.

Name the one you mean however is natural — **by directory** or **by what it
was asked to do**:

> **You:** what's the codex session in the review worktree doing?
>
> **You:** check on the architecture audit session
>
> **You:** what was that session originally asked to do?
>
> **Claude:** It was given the architecture-audit prompt on 21 Aug, on branch
> `feat/tenancy` at commit `06e00be0` — running `gpt-5.6-sol` at low reasoning
> effort.

No UUIDs. Codex stores each session's opening prompt, so a few distinctive
words are enough — which matters when you are on a phone.

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
python3 skills/codex-remote-control/codex_session.py status   # what it's doing now
python3 skills/codex-remote-control/codex_session.py list     # every session
python3 skills/codex-remote-control/codex_session.py meta     # model, git, opening prompt
python3 skills/codex-remote-control/codex_session.py send "…" # queue an instruction
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

## About the name

**Remote Control is Claude Code's feature**, not Codex's — it is what lets you
drive a Claude Code session on your machine from the Claude Code app. Codex has
no equivalent.

This plugin extends that reach: Claude gains the ability to read and address
your Codex sessions, so Codex becomes controllable through Claude's Remote
Control. The name describes the capability it borrows, not one Codex has.

An independent, third-party project. Not affiliated with or endorsed by
Anthropic or OpenAI.

## Requirements

Python 3.7+, standard library only — no packages to install. The `codex` CLI
needs to be on `PATH` to send instructions; reading status does not use it.
Set `CODEX_HOME` if Codex is not at `~/.codex`.

Tested on 3.9 and 3.13. The floor is 3.7 because of
`subprocess.run(capture_output=…)` and `datetime.fromisoformat`; the syntax
itself parses back to 3.6.

## License

MIT
