# AGENTS.md

Orientation for AI coding agents working on this repo.

## Branch policy

- `master` is the release branch. Every push to `master` deploys a new version.
- Never commit or push directly to `master` unless the user explicitly requests
  a release/deployment for the current task.
- All development changes must be pushed to `rc` (or to a development branch
  created from `rc` and then integrated into `rc`).

## What this is

A Codex skill with two adversarial-review backends. The default backend writes
an adversarial prompt, launches Antigravity CLI (agy) (model
`gemini-3.7-flash`, reasoning effort High / high), shows the findings, applies
fixes, and iterates up to 5 rounds. The standalone `self` argument instead
keeps review judgment in the current Codex thread and never launches agy or a
reviewer subagent. This is **not a regular application** — the runtime product
is an instruction file (`SKILL.md`), two workflow specs, one deterministic
runner contract validator, and static regression tests.

## Where to look

- **README.md** — user-facing: install, permissions, troubleshooting.
- **SKILL.md** — the instruction template Codex executes. Uses `${PLACEHOLDER}` syntax for runtime substitution (not shell variables). Step ordering is load-bearing.
- **references/self-review.md** — terminal self-review workflow: local target
  resolution, review method, and report contract.
- **scripts/runner_contract.py** — standard-library-only fail-closed validation
  for prompt structure and the narrowly allowlisted recovered-read signature.
- **docs/DESIGN.md** — the "why" behind every non-obvious decision, rejected alternatives, verification protocol (§7 smoke tests), and the update protocol (§10). Read §10 before modifying SKILL.md.
- **examples/** — sample outputs. Not source of truth.

## Before you change things

- Don't renumber DESIGN.md sections — cross-refs across all three files break silently (§10.2).
- Don't remove the session marker `<!-- ADVERSARIAL-REVIEW-SESSION: ${REVIEW_ID}-${ATTEMPT_ID} -->` from prompt templates — the session-id fallback depends on it (§4.1b).
- Don't add silent-recovery fallbacks. The skill's explicit preference is fail-closed over masked failure (§6.7, §6.8).
- Keep the standalone `self` dispatcher before the placeholder note and Step 1.
  It must stop before any runner, subagent, agy, or `/tmp/agy-*` action.

## Verifying a change

- Smoke test: `docs/DESIGN.md §7` — copy-paste bash, including
  `python3 scripts/test_runner_contract.py` and
  `python3 scripts/test_self_mode_contract.py`, runs in ~2 minutes.
- End-to-end: dogfood via `/adversarial-review code` against any branch with commits vs master.
- No automated CI (§9.6 explains why).

## Architecture

The external backend runs in two processes; the self backend remains entirely
in the current main process:

**Main orchestrator** (`SKILL.md`, main Codex thread): mode detection, REVIEW_ID, REPO_ROOT capture, review-material prep (Steps 1-3), review display (Step 5), code fixes (Step 6), final summary (Step 8), cleanup (Step 9), and round counting.

**Self backend** (`references/self-review.md`, current main Codex thread):
terminal early branch before Step 1. It resolves an explicit or auto-detected
target, performs read-only adversarial inspection, and optionally runs targeted
checks.

**Runner subagent** (`references/runner.md`, dispatched via Agent subagent tool): validates that the prompt contains the required static-review-only policy and absolute repository context, builds the launch prompt with per-attempt session marker, invokes Antigravity (`agy` CLI with model `gemini-3.7-flash`, reasoning effort `high`) with layered workspace binding, runs strict checks on the result, captures the conversation id via two-tier lookup (primary single-JSON `conversation_id`, secondary transcript content-match under `~/.gemini/antigravity-cli/brain/`), and spends one internal retry on any retryable failure. Security-critical prompt validation and the narrow marker-bound missing-file completion predicate are deterministic code in `scripts/runner_contract.py`, with negative fixtures in `scripts/test_runner_contract.py`.

**Why the split:** Every Antigravity invocation produces a stdout JSON object, a stderr file, and a transcript under `~/.gemini/antigravity-cli/brain/`. Keeping these inside the subagent means the main thread's context never sees them — only the final review markdown (~5K) flows back. This eliminates context residue across review rounds.

**Boundary invariants:**
- Self mode never invokes agy, the runner, `codex exec`, or any reviewer or
  delegation subagent, and never falls through to Steps 1–9.
- Self mode is read-only.
- Main never reads `/tmp/agy-stdout-*`, `/tmp/agy-stderr-*`, or any rollout file directly.
- Subagent never interprets findings, applies fixes, or decides whether to start another round.
- The review file at `/tmp/agy-review-${REVIEW_ID}.md` is the sole artifact that crosses the boundary.
- Every initial, fresh-exec, and resume prompt limits Antigravity to static diff/file inspection; builds, compilation, tests, and other project execution are forbidden even when repository-local instructions request them.
