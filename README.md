# Adversarial Review

Codex skill for adversarial AI code and plan review.

One AI writes the code (Codex master). Another tears it apart (Antigravity `gemini-3.7-flash`, high effort reviewer). Iterate until approved.

## What is this

Most AI code review tools validate your changes — "looks good, maybe add tests."
Adversarial review does the opposite: the reviewer **defaults to skepticism**
and tries to break confidence in the change. It looks for what will fail
in production, not what might be nice to improve.

This is a Codex skill — a `SKILL.md` file plus a small `references/runner.md` that together
teach OpenAI Codex how to run adversarial reviews through Antigravity CLI (agy)
(model `gemini-3.7-flash`, reasoning effort `high`).

## Key features

- **Plan review** — review the plan BEFORE writing code. Catch architecture
  mistakes, missing steps, and risks early
- **Code review** — review the implementation. Bugs, security, data loss
- **Code-vs-plan** — verify the implementation matches the plan
- **Iterative** — Codex fixes issues based on reviewer feedback and resubmits
  for re-review. Up to 5 rounds until approved
- **Lightweight** — `SKILL.md` + one `references/runner.md` (thin runner
  subagent spec), no server, no broker, no external runtime deps.

## How it works

```
┌─────────┐     ┌──────────┐     ┌─────────┐
│  Codex  │────>│ Reviewer  │────>│  Codex  │
│  (code) │     │ (Antigravity) │     │  (fix)  │
└─────────┘     └──────────┘     └─────────┘
     ^                                 │
     │         ┌──────────┐            │
     └─────────│ Reviewer │<───────────┘
               │(re-review)│
               └──────────┘
                    │
              VERDICT: APPROVED
```

### Three modes

| Mode | What it reviews | When to use |
|------|----------------|-------------|
| `plan` | Implementation plan | Before writing code |
| `code` | Git diff (unstaged, staged, or branch) | After writing code |
| `code-vs-plan` | Code changes against the plan | Verify implementation matches plan |

Mode is auto-detected from context, or you can force it with an argument.

## Quick start

### 1. Prerequisites

[OpenAI Codex CLI](https://github.com/openai/codex) and
[Antigravity CLI](https://antigravity.google/docs/cli) (`agy`) must be installed.

Verify both are available:

```bash
codex --version  # OpenAI Codex CLI
agy --version    # Antigravity CLI
```

**Authentication.** Antigravity needs API credentials. Either set `GEMINI_API_KEY` env var or sign in via Antigravity CLI.

### 2. Install the skill

```bash
git clone https://github.com/d00rsfan/adversarial-review.git
cd adversarial-review
mkdir -p ~/.codex/skills
ln -sfn "$(pwd)" ~/.codex/skills/adversarial-review
```

Verify both the skill entry-point AND the runner subagent spec are in place:

```bash
ls -la ~/.codex/skills/adversarial-review/SKILL.md
ls -la ~/.codex/skills/adversarial-review/references/runner.md
```

> **Migrating from a previous install at `~/.claude/skills/` or `~/.agents/skills/`**: delete
> the old symlink (`rm ~/.claude/skills/adversarial-review`) and
> install at the new path above. The skill now uses `~/.codex/skills/`.

### 3. Add permissions

The skill runs `git`, `agy`, and writes temp files to `/tmp`.
Without pre-approved permissions, Codex will prompt for each action.

Permissions should go into `~/.codex/settings.json` (global) or project settings:

```jsonc
// --- adversarial-review permissions ---
// Git: diff, status, branch detection, repo root, submodule check
"Bash(git diff*)",
"Bash(git status*)",
"Bash(git symbolic-ref*)",
"Bash(git rev-parse*)",
// Antigravity: initial/fresh/resume launch (prompt is one --print argument)
"Bash(cd * && timeout 600 agy --print *)",
// Prompt-file reads performed by the quoted command substitutions
"Bash(cat /tmp/agy-prompt-*)",
"Bash(cat /tmp/agy-resume-prompt-*)",
// Conversation-id filesystem fallback (find -newer + grep -l content match)
"Bash(find ~/.gemini/antigravity-cli/brain*)",
"Bash(ls -t ~/.gemini/antigravity-cli/brain*)",
// Temp files: prompts (initial + resume), plans, review output, JSON stdout, stderr
"Write(/tmp/agy-plan-*)",
"Write(/tmp/agy-prompt-*)",
"Write(/tmp/agy-resume-prompt-*)",
"Read(/tmp/agy-review-*)",
"Read(/tmp/agy-stdout-*)",
"Read(/tmp/agy-stderr-*)",
// Archive failed-resume diagnostics before fresh exec overwrites them
"Bash(mv /tmp/agy-stdout-* /tmp/agy-stdout-*-failed-resume.jsonl)",
"Bash(mv /tmp/agy-stderr-* /tmp/agy-stderr-*-failed-resume.txt)",
// Cleanup
"Bash(rm -f /tmp/agy-*)",
// Main thread: write prompt body for the runner subagent
"Write(/tmp/agy-body-*)",
// Main thread: read the structured JSON result returned by the runner
"Read(/tmp/agy-runner-result-*)",
// Runner subagent (inherited): read the prompt body main wrote
"Read(/tmp/agy-body-*)",
// Runner subagent (inherited): write the result JSON main reads
"Write(/tmp/agy-runner-result-*)",
// Runner-spec discovery
"Bash(ls ~/.codex/skills/adversarial-review/references/runner.md*)",
"Bash(ls ~/.codex/plugins/cache/*/*/*/skills/adversarial-review/references/runner.md*)"
```

<details>
<summary>Full example (if the config file is empty or does not exist)</summary>

```jsonc
{
  "permissions": {
    "allow": [
      "Bash(git diff*)",
      "Bash(git status*)",
      "Bash(git symbolic-ref*)",
      "Bash(git rev-parse*)",
      "Bash(cd * && timeout 600 agy --print *)",
      "Bash(cat /tmp/agy-prompt-*)",
      "Bash(cat /tmp/agy-resume-prompt-*)",
      "Bash(find ~/.gemini/antigravity-cli/brain*)",
      "Bash(ls -t ~/.gemini/antigravity-cli/brain*)",
      "Write(/tmp/agy-plan-*)",
      "Write(/tmp/agy-prompt-*)",
      "Write(/tmp/agy-resume-prompt-*)",
      "Read(/tmp/agy-review-*)",
      "Read(/tmp/agy-stdout-*)",
      "Read(/tmp/agy-stderr-*)",
      "Bash(mv /tmp/agy-stdout-* /tmp/agy-stdout-*-failed-resume.jsonl)",
      "Bash(mv /tmp/agy-stderr-* /tmp/agy-stderr-*-failed-resume.txt)",
      "Bash(rm -f /tmp/agy-*)",
      "Write(/tmp/agy-body-*)",
      "Read(/tmp/agy-runner-result-*)",
      "Read(/tmp/agy-body-*)",
      "Write(/tmp/agy-runner-result-*)",
      "Bash(ls ~/.codex/skills/adversarial-review/references/runner.md*)",
      "Bash(ls ~/.codex/plugins/cache/*/*/*/skills/adversarial-review/references/runner.md*)"
    ]
  }
}
```

</details>

**Security note:** The `agy` rule allows any `agy` invocation
wrapped in `timeout 600`. The skill passes `--mode plan`, and every reviewer
prompt explicitly prohibits file changes. It also passes
`--dangerously-skip-permissions` because headless agy cannot interactively
approve the commands needed to inspect a repository. This flag auto-approves
agy tool calls, so use the skill only on repositories you trust,
but Codex's permission patterns are prefix-based and cannot enforce flag
constraints. If you prefer tighter control, omit the `agy` rule and
approve each invocation manually.

### 4. Use

```bash
/adversarial-review                       # auto-detect mode
/adversarial-review plan                  # force plan review
/adversarial-review code                  # force code review
/adversarial-review path/to/f             # review a specific file
/adversarial-review model:gemini-3.7-flash # specify agy model
```

## Prompt architecture

The skill uses XML-structured prompts with adversarial stance:

- **`<role>`** — adversarial reviewer, defaults to skepticism
- **`<operating_stance>`** — break confidence, not validate
- **`<attack_surface>`** — concrete checklist: auth, data integrity,
  race conditions, rollback safety, schema drift, error handling, observability
- **`<finding_bar>`** — every finding must answer 4 questions:
  what can go wrong, why vulnerable, impact, recommendation
- **`<scope_exclusions>`** — no style, naming, or speculative comments
- **`<calibration>`** — one strong finding > five weak ones

## Example output

See [examples/review-output.md](examples/review-output.md) for a sample review.

## Troubleshooting

**Antigravity execution exits with model error.**
Ensure `GEMINI_API_KEY` is exported or Antigravity CLI is authenticated.
The default `gemini-3.7-flash` works with API key auth.
Override with `/adversarial-review model:<name>`.

**Permission prompts on every action.**
Add the permissions from the [setup section](#3-add-permissions). Check that
the file is valid JSON and in the right location (`~/.codex/settings.json`).

**Antigravity hangs / timeout (exit code 124).**
All reviewer calls use `--print-timeout 10m` and are also wrapped in
`timeout 600`. If you see exit code 124, the reviewer did not respond in time.
Retry — this is usually
transient.

**Resume fails with session error.**
The skill uses `agy --conversation <conversation-id>` for rounds 2+. On failure
(non-zero exit, missing-conversation warning, changed conversation id, or a malformed review),
the skill does NOT silently fall back. In an interactive session it asks
whether to run a fresh Antigravity execution (higher token cost) or conclude the
review as NOT VERIFIED. In headless runs it decides based on the maximum
severity of the last successful round's findings: critical/high → fresh
exec; medium-only → conclude as NOT VERIFIED.

**JSON output lacks a conversation ID.**
Antigravity `--output-format json` emits one JSON object whose `response`
becomes `/tmp/agy-review-*.md` and whose `conversation_id` is used for the
next round. If that field is absent or the object cannot be parsed, the skill
uses a filesystem fallback: every prompt includes a **per-launch** session marker
(`<!-- ADVERSARIAL-REVIEW-SESSION: <REVIEW_ID>-<ATTEMPT_ID> -->`) where
`ATTEMPT_ID` is a fresh random integer regenerated for the initial exec,
every retry, every resume, and every fresh-exec fallback. The marker is
written to the rollout transcript on disk. The skill runs
`find ~/.gemini/antigravity-cli/brain -name 'transcript_full.jsonl' -newer
<prompt-file> -exec grep -l <REVIEW_ID>-<ATTEMPT_ID> {} +` to positively
identify this specific launch's rollout and extracts the UUID from the filename.
Zero or multiple matches → the skill fails closed with a diagnostic rather
than silently picking. Resume continues to work normally. All commands are
POSIX (`find -newer`, `-exec grep -l`) and work identically on Linux and
macOS.

**"NOT VERIFIED" result.**
The skill applied fixes but the reviewer did not re-verify them (resume
failed or the operator chose to conclude). This is not an approval —
manually review the applied fixes before merging.

**Running inside a git submodule.**
`git rev-parse --show-toplevel` returns the submodule path, not the parent
repo. The skill warns you and scopes the review to the submodule. If you
meant to review the parent, invoke the skill from the parent working tree.

**Bare repository or not inside a work tree.**
The skill aborts at Step 2 with a clear message. Run it from inside a
git working tree.

**Plan Mode exits when writing temp files.**
In Codex Plan Mode, writing to `/tmp` may trigger a permission prompt
or exit Plan Mode. This is a known limitation. It does not affect
review correctness.

## Known limitations

- **Plan Mode and `/tmp` writes.** Writing review prompts to `/tmp` may trigger
  a permission prompt or cause Plan Mode to exit. Does not affect review correctness.
- **Headless agy tool approvals.** The runner uses `--mode plan` and explicit
  no-write prompts, but must also use `--dangerously-skip-permissions` so agy
  can run repository-inspection commands without an interactive prompt. Treat
  reviewed repositories as trusted input. agy 1.1.12's `--sandbox` is not used
  because repeated real-diff runs ended with sandbox-server connection resets.
- **Explicit conversation resume has no cwd flag.** The skill captures
  `REPO_ROOT` via `git rev-parse --show-toplevel` at Step 2 and prefixes
  every initial, fresh, and `--conversation` launch with
  `cd '<REPO_ROOT>' && ...`. This requires paths without single quotes;
  pathological paths (containing `'`, `"`, `$`, backtick, newline) cause
  the skill to abort at Step 2.
- **Submodule scoping.** When invoked inside a submodule, the review is
  scoped to the submodule — `git rev-parse --show-toplevel` does not walk
  up to the parent. A warning is printed; invoke from the parent repo if
  you want parent scope.
- **macOS end-to-end not tested.** The secondary session-id capture
  uses only POSIX flags (`find -newer FILE`, `-exec CMD {} +`, `grep -l`),
  so it should work identically on macOS as on Linux, but the skill has
  not been end-to-end tested on macOS.

## Roadmap

- [ ] Local model support (Ollama, llama.cpp)
- [ ] CI integration (GitHub Actions)
- [ ] Multi-reviewer mode (parallel review by multiple models)

## Inspiration

Adversarial prompt structure developed after studying
[openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc) (Apache-2.0).

Borrowed ideas: XML-structured prompts, adversarial stance, attack surface
checklist, finding bar, calibration rules.

What we did differently:
- **Iterative loop** — Codex fixes issues and resubmits (not "stop and ask user")
- **Plan review** — reviews plans before code, not just code
- **Minimal install** — `SKILL.md` + one `references/runner.md`, no
  server, no broker, vs 15+ JS modules
- **Verbatim output** — reviewer findings shown as-is, not rephrased

## License

Apache-2.0 — see [LICENSE](LICENSE).
