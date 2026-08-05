# AGENTS.md

Orientation for AI coding agents working on this repo.

## What this is

A Codex skill that orchestrates adversarial review: Codex writes an adversarial prompt, launches Google Gemini (model `gemini-3.6-flash`, reasoning effort Extra High / xhigh) as an external reviewer, shows the findings to the user, applies fixes, and iterates up to 5 rounds until approved. This is **not a regular codebase** — the product is a single instruction file (`SKILL.md`) executed by OpenAI Codex at runtime.

## Where to look

- **README.md** — user-facing: install, permissions, troubleshooting.
- **SKILL.md** — the instruction template Codex executes. Uses `${PLACEHOLDER}` syntax for runtime substitution (not shell variables). Step ordering is load-bearing.
- **docs/DESIGN.md** — the "why" behind every non-obvious decision, rejected alternatives, verification protocol (§7 smoke tests), and the update protocol (§10). Read §10 before modifying SKILL.md.
- **examples/** — sample outputs. Not source of truth.

## Before you change things

- Don't renumber DESIGN.md sections — cross-refs across all three files break silently (§10.2).
- Don't remove the session marker `<!-- ADVERSARIAL-REVIEW-SESSION: ${REVIEW_ID}-${ATTEMPT_ID} -->` from prompt templates — the session-id fallback depends on it (§4.1b).
- Don't add silent-recovery fallbacks. The skill's explicit preference is fail-closed over masked failure (§6.7, §6.8).

## Verifying a change

- Smoke test: `docs/DESIGN.md §7` — copy-paste bash, runs in ~2 minutes.
- End-to-end: dogfood via `/adversarial-review code` against any branch with commits vs master.
- No automated CI (§9.6 explains why).

## Architecture

The skill runs in two processes:

**Main orchestrator** (`SKILL.md`, main Codex thread): mode detection, REVIEW_ID, REPO_ROOT capture, review-material prep (Steps 1-3), review display (Step 5), code fixes (Step 6), final summary (Step 8), cleanup (Step 9), and round counting.

**Runner subagent** (`references/runner.md`, dispatched via Agent subagent tool): builds the launch prompt with per-attempt session marker, invokes Gemini (`gemini` CLI with model `gemini-3.6-flash`, reasoning effort `xhigh`), runs strict checks on the result, captures the session id via two-tier lookup (primary JSONL `thread_id`/`session_id`, secondary rollout content-match under `~/.gemini/sessions/`), retries once on infrastructure failure, returns a small JSON summary.

**Why the split:** Every Gemini invocation produces stdout JSONL, a stderr file, and a rollout file under `~/.gemini/sessions/`. Keeping these inside the subagent means the main thread's context never sees them — only the final review markdown (~5K) flows back. This eliminates context residue across review rounds.

**Boundary invariants:**
- Main never reads `/tmp/gemini-stdout-*`, `/tmp/gemini-stderr-*`, or any rollout file directly.
- Subagent never interprets findings, applies fixes, or decides whether to start another round.
- The review file at `/tmp/gemini-review-${REVIEW_ID}.md` is the sole artifact that crosses the boundary.
