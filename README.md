# Adversarial Review

Codex skill for adversarial AI code and plan review.

By default, one AI writes the code (Codex master) and another tears it apart
(Antigravity `gemini-3.7-flash`, high effort reviewer). Add `self` when the
current Codex thread should perform the adversarial review itself without
launching `agy` or a reviewer subagent.

## What is this

Most AI code review tools validate your changes — "looks good, maybe add tests."
Adversarial review does the opposite: the reviewer **defaults to skepticism**
and tries to break confidence in the change. It looks for what will fail
in production, not what might be nice to improve.

This is a Codex skill. `SKILL.md` dispatches either to the self-review workflow
in `references/self-review.md`, or to `references/runner.md` plus a
standard-library Python contract validator for reviews through Antigravity CLI
(agy) (model `gemini-3.7-flash`, reasoning effort `high`).

## Key features

- **Plan review** — review the plan BEFORE writing code. Catch architecture
  mistakes, missing steps, and risks early
- **Code review** — review the implementation. Bugs, security, data loss
- **Code-vs-plan** — verify the implementation matches the plan
- **Self review** — add the standalone `self` argument to keep review judgment
  in the current Codex thread; no `agy`, runner, reviewer subagent, or
  `/tmp/agy-*` artifacts
- **Static-only reviewer** — Antigravity reads diffs and source, searches
  references, and traces code paths, but does not build, compile, test, lint,
  format, install dependencies, or run project code
- **Iterative** — Codex fixes issues based on reviewer feedback and resubmits
  for re-review. Up to 5 rounds until approved
- **Lightweight** — instruction file, thin runner subagent spec, and a
  standard-library-only Python helper; no server, broker, or third-party Python
  packages.

## How it works

The default external backend uses this loop:

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

With `$adversarial-review self`, the current Codex thread performs the same
adversarial inspection directly and returns one read-only report. It never
enters the external loop.

### Three modes

| Mode | What it reviews | When to use |
|------|----------------|-------------|
| `plan` | Implementation plan | Before writing code |
| `code` | Git diff (unstaged, staged, or branch) | After writing code |
| `code-vs-plan` | Code changes against the plan | Verify implementation matches plan |

Mode is auto-detected from context, or you can force it with an argument.

## Quick start

### 1. Prerequisites

[OpenAI Codex CLI](https://github.com/openai/codex) is required. Python 3.10 or
newer is used by the repository's deterministic validators. The default
external backend additionally requires
[Antigravity CLI](https://antigravity.google/docs/cli) (`agy`); self mode does
not.

Verify the tools for the backend you intend to use:

```bash
codex --version    # required
python3 --version  # repository validators
agy --version      # external backend only; omit for self mode
```

**External-backend authentication.** Antigravity needs API credentials. Either
set `GEMINI_API_KEY` or sign in via Antigravity CLI. Self mode does not use
these credentials.

### 2. Install the skill

```bash
git clone https://github.com/d00rsfan/adversarial-review.git
cd adversarial-review
mkdir -p ~/.codex/skills
ln -sfn "$(pwd)" ~/.codex/skills/adversarial-review
```

Verify the skill entry point, both workflow specs, and executable contract
helpers are all in place:

```bash
ls -la ~/.codex/skills/adversarial-review/SKILL.md
ls -la ~/.codex/skills/adversarial-review/references/self-review.md
ls -la ~/.codex/skills/adversarial-review/references/runner.md
ls -la ~/.codex/skills/adversarial-review/scripts/runner_contract.py
python3 ~/.codex/skills/adversarial-review/scripts/runner_contract.py --help
python3 ~/.codex/skills/adversarial-review/scripts/test_self_mode_contract.py
```

> **Migrating from a previous install at `~/.claude/skills/` or `~/.agents/skills/`**: delete
> the old symlink (`rm ~/.claude/skills/adversarial-review`) and
> install at the new path above. The skill now uses `~/.codex/skills/`.

### 3. Add permissions

Self mode needs ordinary read-only repository access. It does not run `agy`,
the runner helper, or create `/tmp/agy-*` files.

The external backend runs `git`, `agy`, the installed standard-library Python
helper, and writes temp files to `/tmp`. Without pre-approved permissions,
Codex will prompt for each action.

Permissions should go into `~/.codex/settings.json` (global) or project settings:

```jsonc
// --- adversarial-review permissions ---
// Git: diff, status, branch detection, repo root, submodule check
"Bash(git diff*)",
"Bash(git status*)",
"Bash(git symbolic-ref*)",
"Bash(git rev-parse*)",
// Antigravity: initial/fresh/resume/recovery launch (prompt is one --print argument)
"Bash(cd * && timeout 600 agy --print *)",
// Prompt-file reads performed by the quoted command substitutions
"Bash(cat /tmp/agy-prompt-*)",
"Bash(cat /tmp/agy-resume-prompt-*)",
"Bash(cat /tmp/agy-recovery-prompt-*)",
// Conversation-id filesystem fallback (find -newer + grep -l content match)
"Bash(find ~/.gemini/antigravity-cli/brain*)",
"Bash(ls -t ~/.gemini/antigravity-cli/brain*)",
// Temp files: prompts (initial + resume + recovery), plans, review output, JSON stdout, stderr
"Write(/tmp/agy-plan-*)",
"Write(/tmp/agy-prompt-*)",
"Write(/tmp/agy-resume-prompt-*)",
"Write(/tmp/agy-recovery-prompt-*)",
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
"Write(/tmp/agy-original-body-*)",
"Write(/tmp/agy-resume-body-*)",
// Main thread: read the structured JSON result returned by the runner
"Read(/tmp/agy-runner-result-*)",
// Runner subagent (inherited): read the prompt body main wrote
"Read(/tmp/agy-body-*)",
"Read(/tmp/agy-original-body-*)",
"Read(/tmp/agy-resume-body-*)",
// Runner subagent: deterministic prompt/recovery contract
"Bash(python3 *adversarial-review/scripts/runner_contract.py*)",
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
      "Bash(cat /tmp/agy-recovery-prompt-*)",
      "Bash(find ~/.gemini/antigravity-cli/brain*)",
      "Bash(ls -t ~/.gemini/antigravity-cli/brain*)",
      "Write(/tmp/agy-plan-*)",
      "Write(/tmp/agy-prompt-*)",
      "Write(/tmp/agy-resume-prompt-*)",
      "Write(/tmp/agy-recovery-prompt-*)",
      "Read(/tmp/agy-review-*)",
      "Read(/tmp/agy-stdout-*)",
      "Read(/tmp/agy-stderr-*)",
      "Bash(mv /tmp/agy-stdout-* /tmp/agy-stdout-*-failed-resume.jsonl)",
      "Bash(mv /tmp/agy-stderr-* /tmp/agy-stderr-*-failed-resume.txt)",
      "Bash(rm -f /tmp/agy-*)",
      "Write(/tmp/agy-body-*)",
      "Write(/tmp/agy-original-body-*)",
      "Write(/tmp/agy-resume-body-*)",
      "Read(/tmp/agy-runner-result-*)",
      "Read(/tmp/agy-body-*)",
      "Read(/tmp/agy-original-body-*)",
      "Read(/tmp/agy-resume-body-*)",
      "Bash(python3 *adversarial-review/scripts/runner_contract.py*)",
      "Write(/tmp/agy-runner-result-*)",
      "Bash(ls ~/.codex/skills/adversarial-review/references/runner.md*)",
      "Bash(ls ~/.codex/plugins/cache/*/*/*/skills/adversarial-review/references/runner.md*)"
    ]
  }
}
```

</details>

**Security note:** The `agy` rule allows any `agy` invocation wrapped in
`timeout 600`. The skill passes `--mode plan`, and every initial, fresh, and
resume prompt explicitly prohibits file changes and project verification.
Antigravity may use only the supplied read-only `git diff` commands plus file
read/search operations; it must not run builds, compilation, tests, lint,
formatting, dependency operations, generators, migrations, project scripts,
applications, services, or containers. Repository-local instructions cannot
override this policy.

The runner also passes `--dangerously-skip-permissions` because headless agy
cannot interactively approve the `git diff` commands needed to inspect a
repository. This flag auto-approves agy tool calls, so the static-only boundary
is prompt-enforced rather than an OS command allowlist. Use the skill only on
repositories you trust. Codex's permission patterns are prefix-based and
cannot enforce flag constraints. If you prefer tighter control, omit the `agy`
rule and approve each invocation manually.

### 4. Use

```bash
/adversarial-review                       # auto-detect mode
/adversarial-review plan                  # force plan review
/adversarial-review code                  # force code review
/adversarial-review path/to/f             # review a specific file
/adversarial-review model:gemini-3.7-flash # specify agy model
$adversarial-review self                   # Codex reviews directly; no agy/subagent
$adversarial-review self code              # self-review local code changes
$adversarial-review self <target>          # self-review an explicit target
```

`self` is a standalone token. `model:*` is intentionally incompatible with
self mode because it selects an external reviewer.

## Prompt architecture

The external backend uses XML-structured prompts, while self mode applies the
same adversarial stance directly from `references/self-review.md`:

- **`<role>`** — adversarial reviewer, defaults to skepticism
- **`<operating_stance>`** — break confidence, not validate
- **`<review_method>`** — static inspection only; no builds, compilation,
  tests, project checks, or `Verification` report
- **`<attack_surface>`** — concrete checklist: auth, data integrity,
  race conditions, rollback safety, schema drift, error handling, observability
- **`<finding_bar>`** — every finding must answer 4 questions:
  what can go wrong, why vulnerable, impact, recommendation
- **`<scope_exclusions>`** — no style, naming, or speculative comments
- **`<calibration>`** — one strong finding > five weak ones

## Example output

See [examples/review-output.md](examples/review-output.md) for a sample review.

## Troubleshooting

**`self` unexpectedly starts the external reviewer.**
Update the installed skill and ensure `self` is a standalone invocation token,
not part of a path or sentence. Run
`python3 scripts/test_self_mode_contract.py`; the test fails if the dispatcher
can fall through to the external Steps 1–9.

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

**Antigravity reports `The stream was interrupted`.**
On agy 1.1.14 a failed headless turn can return `status: ERROR`, an empty
response, a valid `conversation_id`, and no stderr detail. The runner verifies
the per-attempt marker and exact interruption message in that conversation's
transcript, then spends its one existing retry by continuing the same
conversation. It never accepts the empty failed response and never adds a
third invocation. agy may retain the old JSON `ERROR` status after a clean
recovery turn; the runner accepts that result only when the error value is
unchanged, the conversation ID matches, the recovery response has a valid
verdict, and the marker-bound recovery turn has no new transcript error. A
one-line warning is shown before the recovered review.

**Antigravity reports `missing property 'Pattern'`.**
agy 1.1.17 requires every native `find_by_name` call to include a non-empty
`Pattern`, including directory listings (`Pattern: "*"`). Every external
review prompt carries this requirement, and the deterministic prompt contract
rejects a launch if the instruction is absent. The runner does not accept a
completed-looking response whose top-level status remains `ERROR` after a
malformed call.

**Antigravity returns a valid review together with a missing-file ERROR.**
agy 1.1.14 may retain a recovered `view_file` ENOENT as top-level
`status: ERROR` even after the current turn ends with a complete verdict. The
runner accepts only the exact read-only, repository-local missing-file form,
and only on exit 0 when deterministic checks prove canonical containment, that
neither the immutable original task nor the current prompt required the missing
path, and a later successful read recovered a different repository path with
the same filename. The attempt marker,
conversation ID, semantic verdict, and final transcript response must also
match with no later transcript error. It shows a one-line warning. Other tool
errors and arbitrary `ERROR` responses still fail closed.

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

- **Same-model bias in self mode.** Self review removes the external dependency
  and delegation overhead, but it does not provide the independent-model
  perspective of the default Antigravity backend. Use the external backend
  when diversity of blind spots matters more than speed or availability.
- **Plan Mode and `/tmp` writes.** Writing review prompts to `/tmp` may trigger
  a permission prompt or cause Plan Mode to exit. Does not affect review correctness.
- **Headless agy tool approvals.** The runner uses `--mode plan` and explicit
  no-write/static-only prompts, but must also use
  `--dangerously-skip-permissions` so agy can run the supplied `git diff`
  commands without an interactive prompt. The ban on builds, tests, and other
  project execution is therefore prompt-enforced, not an OS-level command
  boundary. Treat reviewed repositories as trusted input. agy 1.1.12's
  `--sandbox` is not used because repeated real-diff runs ended with
  sandbox-server connection resets.
- **Explicit conversation resume has no cwd flag.** The skill captures
  `REPO_ROOT` via `git rev-parse --show-toplevel`, prefixes every launch with
  `cd '<REPO_ROOT>' &&`, registers it with `--add-dir`, supplies it in the
  reviewer prompt, pins diff commands with `git -C`, and uses absolute paths
  for repository file tools. The layered binding is necessary because agy
  1.1.14 can give its native command tool a scratch cwd despite the process
  `cd`. This requires paths without single quotes; pathological paths
  (containing `'`, `"`, `$`, backtick, newline) cause the skill to abort at
  Step 2.
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
- **Minimal install** — `SKILL.md`, one runner spec, and one
  standard-library Python validator; no server or broker, vs 15+ JS modules
- **Verbatim output** — reviewer findings shown as-is, not rephrased

## License

Apache-2.0 — see [LICENSE](LICENSE).
