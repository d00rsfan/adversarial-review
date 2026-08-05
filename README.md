# Adversarial Review

Codex skill for adversarial AI code and plan review.

One AI writes the code (Codex master). Another tears it apart (Gemini 3.6 Flash Extra High effort reviewer). Iterate until approved.

## What is this

Most AI code review tools validate your changes — "looks good, maybe add tests."
Adversarial review does the opposite: the reviewer **defaults to skepticism**
and tries to break confidence in the change. It looks for what will fail
in production, not what might be nice to improve.

This is a Codex skill — a `SKILL.md` file plus a small `references/runner.md` that together
teach OpenAI Codex how to run adversarial reviews through Google Gemini
(model `gemini-3.6-flash`, reasoning effort `xhigh`).

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
│  (code) │     │ (Gemini) │     │  (fix)  │
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
Google Gemini CLI (`gemini`) must be installed.

Verify both are available:

```bash
codex --version    # OpenAI Codex CLI
gemini --version   # Google Gemini CLI
```

**Authentication.** Gemini needs API credentials. Either set `GEMINI_API_KEY` env var or sign in via Gemini CLI.

### 2. Install the skill

```bash
git clone https://github.com/d00rsfan/adversarial-review.git
mkdir -p ~/.codex/skills
ln -sfn "$(pwd)/adversarial-review" ~/.codex/skills/adversarial-review
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

The skill runs `git`, `gemini`, and writes temp files to `/tmp`.
Without pre-approved permissions, Codex will prompt for each action.

Permissions should go into `~/.codex/settings.json` (global) or project settings:

```jsonc
// --- adversarial-review permissions ---
// Git: diff, status, branch detection, repo root, submodule check
"Bash(git diff*)",
"Bash(git status*)",
"Bash(git symbolic-ref*)",
"Bash(git rev-parse*)",
// Gemini: initial launch (uses prompt fed via cat | pipe)
"Bash(cat /tmp/gemini-prompt-* | timeout 600 gemini *)",
// Gemini: resume (cd prefix because resume pins cwd; prompt via cat | pipe)
"Bash(cd * && cat /tmp/gemini-resume-prompt-* | timeout 600 gemini resume *)",
// Session-id filesystem fallback (POSIX: find -newer + grep -l for content-match)
"Bash(find ~/.gemini/sessions*)",
"Bash(ls -t ~/.gemini/sessions*)",
// Temp files: prompts (initial + resume), plans, review output, JSONL stdout, stderr
"Write(/tmp/gemini-plan-*)",
"Write(/tmp/gemini-prompt-*)",
"Write(/tmp/gemini-resume-prompt-*)",
"Read(/tmp/gemini-review-*)",
"Read(/tmp/gemini-stdout-*)",
"Read(/tmp/gemini-stderr-*)",
// Archive failed-resume diagnostics before fresh exec overwrites them
"Bash(mv /tmp/gemini-stdout-* /tmp/gemini-stdout-*-failed-resume.jsonl)",
"Bash(mv /tmp/gemini-stderr-* /tmp/gemini-stderr-*-failed-resume.txt)",
// Cleanup
"Bash(rm -f /tmp/gemini-*)",
// Main thread: write prompt body for the runner subagent
"Write(/tmp/gemini-body-*)",
// Main thread: read the structured JSON result returned by the runner
"Read(/tmp/gemini-runner-result-*)",
// Runner subagent (inherited): read the prompt body main wrote
"Read(/tmp/gemini-body-*)",
// Runner subagent (inherited): write the result JSON main reads
"Write(/tmp/gemini-runner-result-*)",
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
      "Bash(cat /tmp/gemini-prompt-* | timeout 600 gemini *)",
      "Bash(cd * && cat /tmp/gemini-resume-prompt-* | timeout 600 gemini resume *)",
      "Bash(find ~/.gemini/sessions*)",
      "Bash(ls -t ~/.gemini/sessions*)",
      "Write(/tmp/gemini-plan-*)",
      "Write(/tmp/gemini-prompt-*)",
      "Write(/tmp/gemini-resume-prompt-*)",
      "Read(/tmp/gemini-review-*)",
      "Read(/tmp/gemini-stdout-*)",
      "Read(/tmp/gemini-stderr-*)",
      "Bash(mv /tmp/gemini-stdout-* /tmp/gemini-stdout-*-failed-resume.jsonl)",
      "Bash(mv /tmp/gemini-stderr-* /tmp/gemini-stderr-*-failed-resume.txt)",
      "Bash(rm -f /tmp/gemini-*)",
      "Write(/tmp/gemini-body-*)",
      "Read(/tmp/gemini-runner-result-*)",
      "Read(/tmp/gemini-body-*)",
      "Write(/tmp/gemini-runner-result-*)",
      "Bash(ls ~/.codex/skills/adversarial-review/references/runner.md*)",
      "Bash(ls ~/.codex/plugins/cache/*/*/*/skills/adversarial-review/references/runner.md*)"
    ]
  }
}
```

</details>

**Security note:** The `gemini` rule allows any `gemini` invocation
wrapped in `timeout 600`. The skill only uses read-only mode,
but Codex's permission patterns are prefix-based and cannot enforce flag
constraints. If you prefer tighter control, omit the `gemini` rule and
approve each invocation manually.

### 4. Use

```bash
/adversarial-review                       # auto-detect mode
/adversarial-review plan                  # force plan review
/adversarial-review code                  # force code review
/adversarial-review path/to/f             # review a specific file
/adversarial-review xhigh                 # extra high reasoning effort (default)
/adversarial-review model:gemini-3.6-flash # specify gemini model
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

**Gemini execution exits with model error.**
Ensure `GEMINI_API_KEY` is exported or Gemini CLI is authenticated.
The default `gemini-3.6-flash` works with API key auth.
Override with `/adversarial-review model:<name>`.

**Permission prompts on every action.**
Add the permissions from the [setup section](#3-add-permissions). Check that
the file is valid JSON and in the right location (`~/.codex/settings.json`).

**Gemini hangs / timeout (exit code 124).**
All reviewer calls are wrapped in `timeout 600` (10 minutes). If you see
exit code 124, the reviewer did not respond in time. Retry — this is usually
transient.

**Resume fails with session error.**
The skill uses `gemini resume <session-id>` for rounds 2+. On failure
(non-zero exit, `thread/resume failed` in stderr, or a malformed review),
the skill does NOT silently fall back. In an interactive session it asks
whether to run a fresh Gemini execution (higher token cost) or conclude the
review as NOT VERIFIED. In headless runs it decides based on the maximum
severity of the last successful round's findings: critical/high → fresh
exec; medium-only → conclude as NOT VERIFIED.

**JSON stdout is empty (session ID capture noise).**
In some sandbox configurations `--json` event stream is
suppressed when stdout is redirected to a file — `/tmp/gemini-stdout-*.jsonl`
ends up 0 bytes even though the review itself (`-o /tmp/gemini-review-*.md`)
completes correctly. The skill handles this automatically via a filesystem
fallback: every prompt includes a **per-launch** session marker
(`<!-- ADVERSARIAL-REVIEW-SESSION: <REVIEW_ID>-<ATTEMPT_ID> -->`) where
`ATTEMPT_ID` is a fresh random integer regenerated for the initial exec,
every retry, every resume, and every fresh-exec fallback. The marker is
written to the rollout JSONL on disk. When the JSONL stream is empty, the
skill runs `find ~/.gemini/sessions -name 'rollout-*.jsonl' -newer
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
- **`resume` inherits sandbox.** `gemini resume` inherits sandbox settings
  from the original session (always `read-only`).
- **`resume` has no `-C` flag.** The skill captures `REPO_ROOT` via
  `git rev-parse --show-toplevel` at Step 2 and prefixes every resume with
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
