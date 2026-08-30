# awfocus

<!-- aither-header:start GENERATED from the ecosystem registry. Edits here are overwritten; change the registry instead. -->

**[Docs](https://aitherium.github.io/awfocus/)**  ·  [Source](https://github.com/Aitherium/awfocus)  ·  `pip install awfocus`  ·  [The Aither World](https://aitherium.github.io/)

> **The Aither World** is an operating system for agents — a Linux you can hand to one, the runtimes it works in, and the tools it works with. [awnix](https://github.com/Aitherium/awnix) is the Linux underneath it; **awfocus** is one of its 34 bricks — each installs on its own, runs offline, and needs no account.
>
> **Start here:** Stop hunting through terminal tabs. One command names every session, finds any transcript, and opens or messages the one you want.

<!-- aither-header:end -->

## What it is

Every Claude Code session is a tab, and nothing answers *"which session is doing
what right now, where did I say X, how do I tell that session to do Y"* — so you
squint at a wall of terminals. awfocus reads the files the presence plane
already writes and gives you one command:

```bash
awfocus                      # list what is running right now
awfocus what did we decide about the tunnel
                             # search every transcript, ranked
awfocus open 8d191d35        # open that session in a terminal tab
awfocus ask 8d191d35 "ship it"
                             # deliver a message into the session
awfocus remote               # sessions seen on the relay (other machines)
awfocus --self-test          # prove the contract, offline
```

A bare first word that is not a command is a search — `awfocus <query>` is the
point of this tool, so it is never a typo of a subcommand.

## Where the data comes from

awfocus lifts nothing and runs no service. It reads:

| source | what it gives |
|---|---|
| `~/.aither/claude_sessions/live.json` | the live registry (id, title, cwd, branch, last prompt) — maintained by the `aither-presence` SessionStart/Stop hook, pid-validated |
| `~/.claude/sessions/<pid>.json` | per-process status (busy/idle) and name |
| `~/.claude/projects/*/<id>.jsonl` | every transcript, searched line-by-line |
| `~/.aither/steer/<id>/` | the mailbox the `awask` drain hook already reads — `awfocus ask` drops a message there, and it reaches the session at its next prompt |
| the `#claude-sessions` relay channel | `awfocus remote` — sessions on other machines, via `awrelay history` |

A session that is already live is never re-opened (`awfocus open` refuses), and
the tab it opens uses the same encoded-command spawn the AitherResume engine
uses — colour-suppression vars scrubbed, `wt`'s semicolon splitting avoided.

## The ask half

`awfocus ask` writes `<timestamp>-awfocus.md` into the session's steer mailbox
with a `<!-- awfocus:question -->` marker first line, so the drain hook frames
it as an *owner question* rather than a decision answer. It is archived to
`delivered/` after injection, so it is never delivered twice.

## Self-test

```bash
awfocus --self-test
```

Proves all four halves offline: registry parsing and id/title resolution,
transcript search + project filter, mailbox writes + traversal refusal, and
the tab-spawn payload shape — including the already-live refusal.

## Licence

Apache-2.0.
