# a2a-experiments

A deterministic dev rig for A2A (Agent2Agent protocol) apps, built on top of
[a2acode](https://github.com/kanywst/a2acode), a Claude-Code-over-A2A server.

The idea: instead of mocking the A2A protocol, run the real server with a scripted brain
instead of a live model. A `playback` backend reads a scenario file and emits the exact same
events — task states, artifacts, permission pauses — that a real Claude Code run would, over
the real JSON-RPC/SSE surface. Frontends and agents get a fast, predictable, protocol-correct
target to develop against, and the fake can't drift from the real producer because it's built
out of the same server and protocol mapping, just with the model swapped out.

## Layout

- **`docs/`** — design, execution plan, and running log. Start with `docs/PLAN.md` for current
  status, `docs/DEVLOG.md` for the narrative behind it, and `docs/DESIGN-v3.md` for the target
  architecture. `CLAUDE.md` explains how these three documents divide the work.
- **`a2a-rig/`** — the actual code: the `playback` backend and a pytest harness that drives
  a2acode over the real A2A surface. See `a2a-rig/README.md` for how to run it.

`a2a-rig/` is incubating here rather than living in its own repo. It's early enough that
keeping it next to the docs that motivate it is more useful than the isolation of a separate
repo would be; it's expected to get split back out (with full history, via `git subtree`) once
it's further along.

## Status

Early and exploratory. Phases 0 through 4 are done: a2acode is running locally, an independent
client has driven the full A2A surface (including a real, paid Claude Code run for reference),
and the pytest harness runs green against both a2acode's echo backend and the new playback
backend, sub-second and offline. See `docs/PLAN.md` for what's next.

## Prerequisites

You'll need a checkout of [a2acode](https://github.com/kanywst/a2acode) alongside this repo
(pinned at `v0.6.2`) — see `CLAUDE.md` for the sibling-checkout layout and how to run it.
