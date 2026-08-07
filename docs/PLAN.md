# Execution Plan — Zero to a Deterministic A2A Dev Rig

*Companion to DESIGN-v3. Principle: every phase ends with something you can poke at with an
independent client, so we always know the stack works in general before adding the next
layer. Phases 0–1 and 4+ need no API key; phase 2 is the only one that spends money.*

Each phase lists **where it runs** — several can be done in a cloud sandbox/CI; anything
touching your Anthropic credential belongs on your machine.

---

## Phase 0 — Environment and install *(anywhere; ~30 min)* ✅ done 2026-08-06

Goal: a2acode running from source, its own tests green, client tools on hand.

- [x] Prereqs: Python 3.13 (not 3.14-rc — pydantic breaks on the RC), `uv`, Node 20+ (for
      `npx`-launched ACP adapters and a2a-cli).
- [x] `git clone https://github.com/kanywst/a2acode && cd a2acode && uv sync --dev`
      (pin the commit; v0.6.2 is the assessed baseline). Cloned to `~/github.com/kanywst/a2acode`,
      already at the v0.6.2 tag.
- [x] Verify: `uv run pytest -q` → expect **163 passed** (verified 2026-08-05, reconfirmed 2026-08-06).
- [x] Install clients: `npm install -g a2a-cli`; clone
      [a2a-inspector](https://github.com/a2aproject/a2a-inspector) (`uv sync`,
      `cd frontend && npm install`). **Note:** the published `a2a-cli` npm package can't talk to
      a2acode's card shape (SDK version skew — see DEVLOG.md 2026-08-06). It now runs from a
      patched fork (`~/github.com/technicalpickles/a2a-cli`, branch `a2a-sdk-1.0-migration`)
      via `npm link`, not the plain npm install.

**Exit:** tests green; `a2acode --help`, `a2a-cli --help`, inspector builds. ✅

## Phase 1 — First light: echo backend + existing clients *(anywhere; no key; ~1 hr)* ✅ done 2026-08-06

Goal: see the full A2A surface working end to end, with clients we didn't write — so later
failures are attributable to our code, not the stack.

- [x] `uv run a2acode serve --backend echo` (port 9100).
- [x] Card check: `uv run a2acode card`, and raw:
      `curl -s localhost:9100/.well-known/agent-card.json | jq .` — note skills, streaming,
      pushNotifications. Confirmed `streaming: true`, `pushNotifications: true`, 6 skills.
- [x] Built-in client: `uv run a2acode call "hello world"` → task id, context id, streamed
      echo, `[completed]`.
- [x] **Existing TUI:** `a2a-cli chat --server http://localhost:9100/` — interactive session;
      `/new` to reset; also `a2a-cli send "hi" --wait` and `a2a-cli get <task-id>`. Required
      patching `a2a-cli` to the current `@a2a-js/sdk` (see note above and DEVLOG.md) — the
      published package can't fetch a2acode's card at all.
- [ ] **Inspector:** run it, point at `http://localhost:9100/`, confirm card validation
      passes and watch the raw JSON-RPC/SSE in its debug console while chatting. **Deferred:**
      same SDK-skew root cause as a2a-cli, but a2a-inspector's client is built on `a2a-sdk`
      1.1.2's protobuf-generated message types internally, so fixing it is a real rewrite, not
      a like-for-like patch. Not blocking — a2a-cli alone satisfies the exit criterion below.
- [x] Exercise the pause: send a prompt containing `sudo` → task parks in `input-required`;
      answer with `a2acode call "allow" --task <id> --context <id>` (and once with a denial).
      Verified via both `a2acode call` and the patched `a2a-cli chat`.
- [x] Multi-turn: two `call`s sharing `--context`; confirm the second reports continuity.
- [ ] Optional sanity (once, not CI): `a2a-tck --sut-host http://localhost:9100` MUST tier;
      record results as a baseline. Skipped for now (optional).

**Exit:** an independent client (a2a-cli or inspector) has driven card discovery, streaming,
`input-required` round trip, and multi-turn against a2acode. **This is the checkpoint that
the A2A stack "works in general."** ✅ Met via `a2a-cli` (patched fork); inspector left for later.

## Phase 2 — One real-inference sanity pass *(your machine; API key; ~$1 budget)* ✅ done 2026-08-07

Goal: prove the same surface carries a *real* Claude Code run — so playback scenarios have a
known-real reference — then stop spending.

- [x] Scratch repo: a tiny git repo (a 20-line Flask app or similar), committed clean.
      `~/scratch/demo-app` at `6890fd7` — Flask app with `/items` and `/items/<id>`.
- [x] Serve the SDK path: `ANTHROPIC_API_KEY=… uv run a2acode serve --backend claude
      --cwd ~/scratch/demo-app --max-budget-usd 1` (add `--permission-mode acceptEdits` only
      if you want fewer pauses; default routes tool approvals to you — more instructive).
      **No API key needed** — the backend inherits the `claude` CLI's own auth, and
      subscription credentials still report real `cost_usd`. Ran without `--max-budget-usd`.
- [x] From a2a-cli or `a2acode call`: `"add a /health endpoint returning ok"`.
      Watch: plan artifact, tool-call status updates, permission prompts (approve via
      follow-up message), file-diff artifact, cost metadata on completion.
      All present **except the plan artifact** — Claude never called `TodoWrite` on a task
      this small, so no `plan` event was emitted. Driven via `a2a-cli chat` first, then a
      JSON-dumping client for the wire shapes (`docs/captures/dump_stream.py`).
- [x] Verify on disk: `git -C ~/scratch/demo-app diff` matches the artifact.
- [x] Multi-turn: `"now add a test for it"` with the same `--context` → session resumes.
      New task id under the same context, same `claude_session_id`.
- [x] Save the terminal transcript / inspector capture — the shape reference for Phase 5
      scenarios (and later the `--record` flag replaces this manual capture).
      → `docs/captures/phase2-claude-run.jsonl` (66 wire-level events).
- [ ] Optional: repeat once via the default ACP path (`--backend acp`,
      needs `npx @zed-industries/claude-agent-acp`) to see the vendor-neutral route.
      Skipped for now (optional).

**Exit:** one end-to-end real run observed through the same clients, artifacts verified on
disk, transcript saved. Real inference is now optional for everything below. ✅
Total spend: **$0.54** across two turns.

## Phase 3 — Drive it the way your code will *(anywhere; no key; ~half a day)*

Goal: a pytest harness using the official A2A client — written against echo now, reused
verbatim against playback later (and against real backends whenever wanted). This is the
skeleton your agents' own tests will grow from.

- [ ] New repo (`a2a-rig/` per DESIGN-v3 §7 layout): `a2a-sdk` + pytest + pytest-asyncio.
- [ ] Fixtures: launch `a2acode serve` as a subprocess on a free port with chosen
      backend/flags; `ClientFactory.create_from_url()` client; teardown kills the server.
- [ ] Tests against echo: card fetch + field assertions; send → collect event stream →
      assert Task-first ordering, artifact chunks, terminal state; `input-required` round
      trip (allow and deny); multi-turn context; cancel mid-stream; `tasks/get` after
      completion.
- [ ] Make the backend a fixture parameter (`echo` today; `playback` next phase — the test
      body shouldn't change).

**Exit:** `pytest` green in seconds against echo; harness is backend-parameterized.

## Phase 4 — Playback M0: first fake repo *(anywhere; ~1–2 days)*

Goal: DESIGN-v3's centerpiece, minimum vocabulary. Out-of-tree, importing a2acode.

- [ ] `playback` Backend: loads scenario YAML; emits `text`, `tool_use`/`tool_result`,
      `result`; match rules `turn`/`contains`/default; unmatched → loud failure.
- [ ] Serve wrapper: `rig-serve --scenario scenarios/billing-api.yaml --port 9200` (thin
      script passing the backend into a2acode's `build_app()`).
- [ ] `billing-api.yaml` hand-written from the Phase 2 transcript's shape.
- [ ] Point the Phase 3 harness at it (backend param flip) — all tests green, now in ms.
- [ ] Poke it with a2a-cli/inspector exactly as in Phase 1.

**Exit:** an independent client and the pytest harness both drive a fake repo with zero
inference and sub-second turns. **From here, frontend development can start for real.**

## Phase 5 — M1: full scenario vocabulary *(anywhere)*

- [ ] `permission` events with `on_allow`/`on_deny`/`timeout_ms` branches.
- [ ] `plan`, `thought`, `file_change`, `notice` events; `error`/`stop_reason` variants.
- [ ] `delay_ms` + `PLAYBACK_SPEED`; cancel honored mid-delay.
- [ ] Harness tests for each (the permission/deny/timeout tests you could never run reliably
      against live inference).

**Exit:** a UI can build chat, plan, diff, and approval views entirely offline.

## Phase 6 — M2: multi-repo rig + your consumers *(anywhere)*

- [ ] Scenario directory → N fake repos: per-port supervisor or the mounted-apps wrapper
      (DESIGN-v2 §9 pattern 2), each with its own card name.
- [ ] Pytest fixtures exposing "a directory of repos" to your agents' tests.
- [ ] **Start building the frontend and agents against this** — the rig is now their
      standing dev environment.

**Exit:** agent tests run against 3+ fake repos in <5s total; frontend dev loop is offline.

## Phase 7 — M3: the scenario factory *(recording runs on your machine)*

- [ ] `RecordingBackend` decorator + `--record out.yaml` on the serve wrapper.
- [ ] Re-run the Phase 2 prompts once, budget-capped, through `--record`; scrub; check in
      ≥3 recorded scenarios replacing hand-written ones.
- [ ] Document the refresh loop: upstream bump → re-record → diff normalized streams →
      update scenarios/frontend.

**Exit:** the scenario library's backbone is recorded, not imagined.

## Phase 8 — M4: upstream *(anywhere)*

- [ ] PR `playback` + `--record` to a2acode (echo-extension framing, DESIGN-v3 §7).
- [ ] File the side-findings from earlier assessment where still relevant (idle-TTL for the
      ACP pool, permission-wait timeout, configurable caps).

**Exit:** PRs opened; rig unaffected either way (pinned dependency).

---

## Sequencing notes

- Phases 0→1→2 are strictly ordered (each attributes failures for the next). Phase 3 can
  start in parallel with Phase 2 — it only needs echo.
- The only inference spend in the whole plan: Phase 2 (~$1) and Phase 7's recording session.
- Phases 0, 1, 3, 4, 5, 6, 8 are sandbox/CI-friendly: no credentials, no trust decisions —
  good candidates to delegate or run in the background.
- Kill criteria worth honoring: if Phase 1 reveals a2acode's surface is wrong for your
  frontend in some fundamental way, stop and reassess before building playback on top of it.
