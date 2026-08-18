# Adversarial-Review Runner Subagent

> This file is read by a subagent dispatched from `SKILL.md` Step 4 or Step 7. It is NOT loaded in the main thread.

You are a thin runner subagent. Your single job: launch ONE agy execution (initial, resume, or fresh-exec), validate the result, capture the session id, and return a small JSON summary. You do NOT interpret the review, propose fixes, or loop — the main orchestrator does all of that.

## Input contract

The main thread dispatches you via the Agent tool. The prompt contains a YAML-like input block. Parse these fields; if any required field is missing, write an input_error result to the result file (see Output contract) and return.

```yaml
REVIEW_ID: <string, format "{unix_ts}-{8-digit}">
REPO_ROOT: <absolute path, validated by main>
OPERATION: initial | resume | fresh-exec
AGY_MODEL: <e.g. gemini-3.7-flash>  # the model agy CLI launches
RUNNER_CONTRACT_PATH: <absolute path to scripts/runner_contract.py>
PROMPT_BODY_PATH: <absolute path to file containing the review prompt body WITHOUT the session marker; main writes this before dispatch>
ORIGINAL_PROMPT_BODY_PATH: <absolute path to immutable initial review prompt body>
AGY_CONVERSATION_ID: <UUID, required only when OPERATION=resume>
RESULT_PATH: /tmp/agy-runner-result-<REVIEW_ID>.json  # you write the structured result here
```

For `OPERATION=initial` and `OPERATION=fresh-exec`, `AGY_CONVERSATION_ID` is absent (ignore if present).

## Output contract — two-channel

To avoid fragility of JSON-in-final-message, you return results via TWO channels:

**Channel 1 — result file (authoritative).** Write the JSON object below to `${RESULT_PATH}` via Write tool. Main reads this file directly; its bytes are the contract. Do NOT omit any field — use `null` for absent values.

```json
{
  "result": "success" | "launch_failure" | "timeout" | "infra_error" | "input_error",
  "verdict": "APPROVED" | "REVISE" | null,
  "review_file": "/tmp/agy-review-<REVIEW_ID>.md" | null,
  "agy_conversation_id": "<uuid>" | null,
  "attempt_id": "<6-digit>",
  "errors": "<short diagnostic, ≤500 chars>" | null,
  "archived_stdout": "/tmp/agy-stdout-<REVIEW_ID>-failed-resume.jsonl" | null,
  "archived_stderr": "/tmp/agy-stderr-<REVIEW_ID>-failed-resume.txt" | null,
  "user_warning": "<one-line message main should surface to user>" | null
}
```

**Channel 2 — final message (short).** Your FINAL message to main should be a single line:

```
RUNNER_RESULT_AT: <RESULT_PATH>
```

Example: `RUNNER_RESULT_AT: /tmp/agy-runner-result-1711872000-48217593.json`

Main's parser is tolerant: it searches the ENTIRE message for a match of the unanchored regex `RUNNER_RESULT_AT:\s+(\S+)` (first match wins; works inside markdown fences, after preamble, or surrounded by other text). Even so, emitting the spec line cleanly (no fence, no preamble) eliminates edge cases.

If your message lacks the line entirely, main falls back to a filesystem Glob for `/tmp/agy-runner-result-${REVIEW_ID}.json` — the path is deterministic from REVIEW_ID, which main already holds. If the Glob also fails (file not written), main treats the run as `infra_error`.

Rules:
- `result=success` ⇒ `verdict` and `review_file` must be set. `agy_conversation_id` must be set iff `verdict=REVISE` (or null per §2.4.4 on resume zero-find — see Step R4.4).
- `result=timeout` ⇒ reviewer timed out (exit 124). `review_file` may be null.
- `result=launch_failure` ⇒ infrastructure retry (one internal retry) already failed. Main treats this as TERMINAL — it will NOT re-dispatch you. `errors` MUST name the failed check, include parsed JSON `status` / `error` when available, and include the tail of stderr when stderr is non-empty.
- `result=infra_error` ⇒ something outside agy (e.g. `/tmp` not writable).
- `user_warning` is non-null when main should surface a one-line warning to the user (e.g. §2.4.4 zero-find on resume or a marker-bound non-`SUCCESS` recovery).
- Do NOT return the review text in the JSON. Main reads `review_file` directly.

## Step-by-step

### Step R1: Generate ATTEMPT_ID

Generate a fresh 6-digit random integer. Use `printf` with `$RANDOM`:

```bash
printf '%06d\n' $((RANDOM * RANDOM % 1000000))
```

Save the output as `${ATTEMPT_ID}` for this invocation. Generate a NEW ATTEMPT_ID on every retry (Step R5).

### Step R2: Build the launch prompt file

Read `${PROMPT_BODY_PATH}` (main wrote it before dispatching you). Also require
`${ORIGINAL_PROMPT_BODY_PATH}` to be a readable regular file. If either body is
missing or unreadable, write `input_error` and return without launching agy;
the immutable original is required even when the ordinary SUCCESS path would
not otherwise inspect it.

**Fail-closed prompt contract check.** Before writing a launch prompt or
calling agy, require `${RUNNER_CONTRACT_PATH}` to be a readable regular file,
then invoke:

```bash
python3 "${RUNNER_CONTRACT_PATH}" prompt \
  --prompt-body "${PROMPT_BODY_PATH}" \
  --repo-root "${REPO_ROOT}" \
  --operation "${OPERATION}" \
  --review-id "${REVIEW_ID}"
```

Parse its one-line JSON output. Continue only when the command exits 0 and
returns `valid: true`. Otherwise write an `input_error` result whose `errors`
contains the helper's `reason`, return the `RUNNER_RESULT_AT:` line, and do NOT
launch or retry agy. The helper checks exactly one static-review policy and
repository-context block before the first review-scoped
`<!-- ADVERSARIAL-REVIEW-CONTRACT: ${REVIEW_ID} -->` boundary, including all
policy anchors and one exact absolute-root line. Task artifacts, resume fix
summaries, and verbatim history occur after that unpredictable boundary, so
quoted tags or quoted later copies of the boundary cannot corrupt the contract
check.

For `OPERATION=initial` or `OPERATION=fresh-exec`:
- Write `/tmp/agy-prompt-${REVIEW_ID}.md` with first line `<!-- ADVERSARIAL-REVIEW-SESSION: ${REVIEW_ID}-${ATTEMPT_ID} -->` followed by the body.

For `OPERATION=resume`:
- Write `/tmp/agy-resume-prompt-${REVIEW_ID}.md` with the same marker-first structure.

Use the Write tool (not `cat <<EOF` via Bash — Write is simpler and does not have quoting edge cases).

**Bump mtime via a second Write** to ensure the prompt file's mtime is strictly later than any rollout file from a prior attempt. Rewrite the same bytes to the same path (Write tool, not Bash `touch` — Bash may be restricted under inherited Plan Mode; Write is already the tool used for the initial body, so whatever gating applies is already cleared by the first Write).

On systems with coarse mtime granularity (1s), two successive Writes within the same second can produce identical mtimes; the repeat Write forces the kernel to update the mtime. On Plan Mode-inherited subagents the first Write may have already prompted the user; the second Write to the identical path reuses that same permission grant.

Alternatively and equivalently safe: skip the mtime bump entirely and rely on ATTEMPT_ID rotation alone — the positive content-match in Step R4.4 binds on the marker, not solely on `-newer`. If the retry's new ATTEMPT_ID is embedded in the prompt's first line (which it is), no prior rollout can false-match. The `-newer` condition is a second guard, not a primary one. If the repeat-Write approach fails in practice, drop it and rely on content-match + multi-match-aborts.

### Step R3: Launch agy

**Synchronous launch only.** Always invoke the Bash tool with `run_in_background: false` (the default). Never set `run_in_background: true` for this call — if agy runs in background, you will proceed to Step R4 before stdout/stderr/review files are populated, and the stderr-missing check will incorrectly route to `infra_error`.

For `OPERATION=initial` and `OPERATION=fresh-exec`:

```bash
cd "${REPO_ROOT}" && timeout 600 agy --print "$(cat /tmp/agy-prompt-${REVIEW_ID}.md)" \
  --add-dir "${REPO_ROOT}" \
  --model ${AGY_MODEL} --effort high \
  --mode plan --dangerously-skip-permissions \
  --output-format json --print-timeout 10m \
  > /tmp/agy-stdout-${REVIEW_ID}.jsonl \
  2>/tmp/agy-stderr-${REVIEW_ID}.txt
agy_rc=$?

# Extract the review text from the JSON output
python3 -c "import sys, json; print(json.load(sys.stdin).get('response', ''))" < /tmp/agy-stdout-${REVIEW_ID}.jsonl > /tmp/agy-review-${REVIEW_ID}.md 2>/dev/null || true
exit "$agy_rc"
```

For interrupted-stream recovery (Step R5), use the same resume command shape,
but read `/tmp/agy-recovery-prompt-${REVIEW_ID}.md` and pass the positively
bound `${RECOVERY_CONVERSATION_ID}` to `--conversation`:

```bash
cd "${REPO_ROOT}" && timeout 600 agy --print "$(cat /tmp/agy-recovery-prompt-${REVIEW_ID}.md)" \
  --conversation ${RECOVERY_CONVERSATION_ID} \
  --add-dir "${REPO_ROOT}" \
  --model ${AGY_MODEL} --effort high \
  --mode plan --dangerously-skip-permissions \
  --output-format json --print-timeout 10m \
  > /tmp/agy-stdout-${REVIEW_ID}.jsonl \
  2>/tmp/agy-stderr-${REVIEW_ID}.txt
agy_rc=$?

python3 -c "import sys, json; print(json.load(sys.stdin).get('response', ''))" < /tmp/agy-stdout-${REVIEW_ID}.jsonl > /tmp/agy-review-${REVIEW_ID}.md 2>/dev/null || true
exit "$agy_rc"
```

This is still the one and only retry for the dispatch; it does not add a third
agy invocation.

Bash tool `timeout` parameter: `620000` (10 min + headroom).

For `OPERATION=resume`:

```bash
cd "${REPO_ROOT}" && timeout 600 agy --print "$(cat /tmp/agy-resume-prompt-${REVIEW_ID}.md)" \
  --conversation ${AGY_CONVERSATION_ID} \
  --add-dir "${REPO_ROOT}" \
  --model ${AGY_MODEL} --effort high \
  --mode plan --dangerously-skip-permissions \
  --output-format json --print-timeout 10m \
  > /tmp/agy-stdout-${REVIEW_ID}.jsonl \
  2>/tmp/agy-stderr-${REVIEW_ID}.txt
agy_rc=$?

# Extract the review text from the JSON output
python3 -c "import sys, json; print(json.load(sys.stdin).get('response', ''))" < /tmp/agy-stdout-${REVIEW_ID}.jsonl > /tmp/agy-review-${REVIEW_ID}.md 2>/dev/null || true
exit "$agy_rc"
```

Substitute literal values for every `${...}` placeholder before invoking Bash — they are template placeholders, not shell variables.

`agy --print` requires the prompt as an argument. Do NOT pipe the prompt on stdin and do NOT pass a positional `-`: agy 1.1.12 does not consume that stdin as the prompt and may return its generic greeting with exit 0. The quoted command substitution expands to one argument; prompt contents are not re-evaluated as shell syntax. For resume, use `--conversation <UUID>` by itself — do NOT combine it with `--continue`, which selects the most recent conversation instead of naming the intended one.

Run each complete code block as ONE Bash tool call. The `agy_rc` capture and final `exit` preserve agy's exit status across the review-extraction command; do NOT split them or omit the final `exit`.

### Step R4: Post-launch strict checks

Do these in order. Stop and return as soon as one fails.

**Check R4.0: Parse the diagnostic JSON envelope.** Opportunistically parse
`/tmp/agy-stdout-${REVIEW_ID}.jsonl` as one JSON object before checking the
process exit. Save `status`, `error`, `response`, and `conversation_id` when
present. A parse failure is handled by R4.3 if the process otherwise succeeded;
for a non-zero exit it simply means the launch diagnostic has no JSON fields.

Classify the attempt as a **recoverable interrupted stream** only when ALL of
these conditions hold:

- `status` is `ERROR`;
- `response` is absent, empty, or whitespace-only;
- `error` is exactly `Agent execution terminated due to error.`;
- `conversation_id` is a valid UUID;
- for `OPERATION=resume`, that UUID equals the requested
  `${AGY_CONVERSATION_ID}` and stderr does not contain the
  missing-conversation warning;
- the transcript at
  `~/.gemini/antigravity-cli/brain/<conversation_id>/.system_generated/logs/transcript_full.jsonl`
  exists, contains the current
  `ADVERSARIAL-REVIEW-SESSION: ${REVIEW_ID}-${ATTEMPT_ID}` marker, and contains
  `Error: The stream was interrupted. Please continue the task you were working on.`
  after that marker.

This classification never accepts a review. It only permits R5 to spend the
existing retry budget by continuing the marker-bound conversation instead of
throwing its gathered context away. Save its UUID as
`${RECOVERY_CONVERSATION_ID}` and its JSON error as `${RECOVERY_ORIGINAL_ERROR}`.

Separately classify the attempt as a **candidate completed review after a
recoverable read-only missing-file error** only when ALL of these preliminary
conditions hold:

- agy exited 0;
- `status` is `ERROR`;
- `response` is non-empty;
- `error` starts with
  `declaring permissions: cortex tool view_file: convert tool call for permissions: model output error: invalid tool call error (invalid_args) failed to read file: open ${REPO_ROOT}/`
  and ends with `: no such file or directory`;
- `conversation_id` is a valid UUID;
- for `OPERATION=resume`, that UUID equals `${AGY_CONVERSATION_ID}` and stderr
  does not contain the missing-conversation warning;
- that UUID's transcript exists.

Set `RECOVERABLE_READ_ERROR_CANDIDATE=true`. This classification does NOT by
itself accept the review. It only permits R4.3's deterministic contract helper
to validate the current turn. Do not broaden the error pattern to other tools,
permission failures, command failures, or arbitrary `invalid_args` errors.

**Check R4.1: Exit code.**
- `124` → route to retry (Step R5). Same retry budget as any other failure — ONE retry per dispatch total. If retry also returns 124, write `{"result":"timeout",...}` and return. (Retrying on timeout keeps the 2-attempts-per-round invariant consistent across failure types; main treats timeout as terminal just like launch_failure.)
- `≠ 0 and ≠ 124` on the first attempt + R4.0 classified a recoverable interrupted stream → route to the interrupted-stream branch of Step R5.
- `≠ 0 and ≠ 124` during interrupted-stream recovery + the diagnostic JSON has a non-empty `response` → continue to R4.2/R4.3; the marker-bound stale-status guard below will decide whether it is usable.
- Any other `≠ 0 and ≠ 124` → read `/tmp/agy-stderr-${REVIEW_ID}.txt`, route to retry (Step R5). Preserve parsed JSON `status` and `error` in the failure diagnostic even when stderr is empty.
- `0` → proceed.

**Check R4.2: Stderr sanity.** Read `/tmp/agy-stderr-${REVIEW_ID}.txt`.
- File missing → return `{"result":"infra_error","errors":"stderr file missing — /tmp writability?",...}`.
- File contains a line matching `^Error:` or `Failed to write` → route to retry (Step R5).
- For `OPERATION=resume`, file contains a line matching `^warning: conversation .* not found$` → route to retry. agy 1.1.12 otherwise exits 0 and silently starts a new conversation.

**Check R4.3: JSON and review sanity.** Require `/tmp/agy-stdout-${REVIEW_ID}.jsonl` to parse as one JSON object. Parse failure → route to retry. (The `.jsonl` suffix is historical; agy `--output-format json` emits one object.)

- `status == "SUCCESS"` → proceed normally.
- On the interrupted-stream recovery attempt only, `status == "ERROR"` may be
  the sticky status from the interrupted turn. Continue provisionally only if
  `conversation_id` equals `${RECOVERY_CONVERSATION_ID}`, `error` equals
  `${RECOVERY_ORIGINAL_ERROR}`, and `response` is non-empty. Set
  `RECOVERY_STALE_STATUS_CANDIDATE=true`; this is NOT success yet.
- When `RECOVERABLE_READ_ERROR_CANDIDATE=true`, continue provisionally; this
  is NOT success until the deterministic helper below returns `valid: true`.
- Every other `status != "SUCCESS"` → route to retry. Include the parsed
  `status` and `error` in the diagnostic.

For `OPERATION=resume`, also require a valid `conversation_id` equal to the input `${AGY_CONVERSATION_ID}` before inspecting the verdict; a missing or different id routes to retry with `errors: "resume conversation id changed: requested <input>, got <captured-or-missing>"`. For interrupted-stream recovery, compare against `${RECOVERY_CONVERSATION_ID}` instead. This check MUST run even when the response says `VERDICT: APPROVED`.

Only after the JSON checks pass, Read `/tmp/agy-review-${REVIEW_ID}.md`.
- File missing or empty → route to retry (Step R5).
- Does NOT contain a line matching `^VERDICT: (APPROVED|REVISE)$` → route to retry.
- Verdict is `REVISE` AND file contains NO line matching `\[severity:\s*(critical|high|medium)` → route to retry (reviewer format drift).

If `RECOVERY_STALE_STATUS_CANDIDATE=true`, read the exact recovery
conversation transcript and find the LAST `USER_INPUT` record containing the
current recovery marker. Inspect only records after that input. Require:

1. no subsequent record has `type == "ERROR_MESSAGE"`; and
2. the final subsequent `PLANNER_RESPONSE` has `status == "DONE"` and its
   `content` equals the extracted JSON `response` after stripping trailing CR
   and LF characters from both values. Do not normalize any other whitespace.

If either condition fails, the recovery failed closed. If both pass, set
`user_warning` to exactly:
`agy recovered an interrupted response stream; agy retained the prior ERROR status, but the marker-bound recovery turn completed without a new transcript error and returned a valid review`
and proceed.

If `RECOVERABLE_READ_ERROR_CANDIDATE=true`, invoke the deterministic contract
helper below. Substitute literal values; omit `--expected-conversation-id` for
initial/fresh-exec and include it with `${AGY_CONVERSATION_ID}` for resume.

```bash
python3 "${RUNNER_CONTRACT_PATH}" recovered-read \
  --stdout-json "/tmp/agy-stdout-${REVIEW_ID}.jsonl" \
  --transcript "$HOME/.gemini/antigravity-cli/brain/<conversation_id>/.system_generated/logs/transcript_full.jsonl" \
  --prompt-body "${PROMPT_BODY_PATH}" \
  --original-prompt-body "${ORIGINAL_PROMPT_BODY_PATH}" \
  --repo-root "${REPO_ROOT}" \
  --marker "ADVERSARIAL-REVIEW-SESSION: ${REVIEW_ID}-${ATTEMPT_ID}" \
  --operation "${OPERATION}" \
  --agy-exit-code 0 \
  --stderr "/tmp/agy-stderr-${REVIEW_ID}.txt" \
  [--expected-conversation-id "${AGY_CONVERSATION_ID}"]
```

Parse its one-line JSON output. If the command is non-zero or `valid` is not
true, route to retry with the helper's `reason`. If valid, copy its exact
non-empty `warning` into `user_warning` and proceed. The helper proves all of:
exit 0; canonical containment without `.`/`..` traversal; the failed path was
not supplied by either the immutable original task or the current prompt;
marker binding; no later
`ERROR_MESSAGE`; a later successful repository-local `view_file` result for a
different path with the same basename, encoded as the empirical single-call
`PLANNER_RESPONSE/DONE` immediately followed by non-empty `GENERIC/DONE`;
valid verdict semantics; and exact
final `PLANNER_RESPONSE/DONE` equality. The interrupted-stream recovery and
this helper-validated read-only missing-file branch are the ONLY cases where a
non-`SUCCESS` JSON status may produce `result=success`.

- Verdict is `APPROVED` → write this EXACT JSON object to `${RESULT_PATH}` and return the `RUNNER_RESULT_AT:` line:

  For a normal `SUCCESS`, use `null` below. If either non-`SUCCESS` guard
  passed, substitute its exact warning string for that one
  `user_warning` value; never write a descriptive placeholder.

  ```json
  {
    "result": "success",
    "verdict": "APPROVED",
    "review_file": "/tmp/agy-review-<REVIEW_ID>.md",
    "agy_conversation_id": null,
    "attempt_id": "<the current ATTEMPT_ID string>",
    "errors": null,
    "archived_stdout": null,
    "archived_stderr": null,
    "user_warning": null
  }
  ```

- Verdict is `REVISE` → proceed to Check R4.4.

**Check R4.4: Capture session id — two tiers.**

*Primary — single JSON object on stdout:*

Read `/tmp/agy-stdout-${REVIEW_ID}.jsonl`. If the JSON object has a `conversation_id` field matching `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`, save it as the captured id. For `OPERATION=resume`, compare it to the input `${AGY_CONVERSATION_ID}`: if they differ, route to retry with `errors: "resume conversation id changed: requested <input>, got <captured>"`. agy 1.1.12 can exit 0 and create a fresh conversation when the requested id is missing; accepting the new id would silently discard prior review context. If the ids match (or the operation is initial/fresh-exec), write the SAME 9-field JSON shape as the secondary tier's "Exactly one path" branch (below) to `${RESULT_PATH}` and return the `RUNNER_RESULT_AT:` line. If no valid id is present, fall through to the secondary tier.

*Secondary — rollout content-match:*

The anchor file is `/tmp/agy-prompt-${REVIEW_ID}.md` for initial/fresh-exec, or `/tmp/agy-resume-prompt-${REVIEW_ID}.md` for resume.

```bash
find ~/.gemini/antigravity-cli/brain -name 'transcript_full.jsonl' -newer <anchor> -exec grep -l 'ADVERSARIAL-REVIEW-SESSION: ${REVIEW_ID}-${ATTEMPT_ID}' {} + 2>/dev/null
```

**Interpret the result by STDOUT, not exit code.** Split the command's stdout on newlines; count non-empty lines. Empty stdout means ZERO paths regardless of the pipeline's exit status (`find` returning no matches and `grep -l` matching nothing in found files both yield empty stdout with different exit codes; treat both as zero).

- **Exactly one path** → extract the trailing UUID from the filename (the UUID is the directory name directly under `brain`, e.g. `.../brain/<UUID>/.system_generated/...`). For `OPERATION=resume`, require that UUID to equal the input `${AGY_CONVERSATION_ID}`; a mismatch routes to retry with the same `resume conversation id changed` diagnostic as the primary tier. Otherwise write this EXACT JSON object to `${RESULT_PATH}` (all 9 fields explicitly; do NOT leave any omitted or as literal placeholder like `"<uuid>"`):

For the JSON shape below, use `user_warning: null` on normal success. If either
non-`SUCCESS` guard passed, substitute its exact warning string from R4.3;
never write a descriptive placeholder.

```json
{
  "result": "success",
  "verdict": "REVISE",
  "review_file": "/tmp/agy-review-<REVIEW_ID>.md",
  "agy_conversation_id": "<actual 36-char UUID you extracted from the filename>",
  "attempt_id": "<the current ATTEMPT_ID string>",
  "errors": null,
  "archived_stdout": null,
  "archived_stderr": null,
  "user_warning": null
}
```

- **Zero paths for resume** → write this EXACT JSON object (9 fields, `agy_conversation_id` is null, `user_warning` carries the §2.4.4 diagnostic):

```json
{
  "result": "success",
  "verdict": "REVISE",
  "review_file": "/tmp/agy-review-<REVIEW_ID>.md",
  "agy_conversation_id": null,
  "attempt_id": "<ATTEMPT_ID>",
  "errors": null,
  "archived_stdout": null,
  "archived_stderr": null,
  "user_warning": "Step 7 session-id refresh: both tiers empty, continuing with previous ID per DESIGN.md §2.4.4"
}
```

- **Zero paths for initial/fresh-exec** → route to retry (Step R5). Main needs the id to launch next round. Set `errors: "session-id capture failed: both tiers empty on initial/fresh-exec"`.
- **Multiple paths** → write `launch_failure` result with `errors: "multiple rollouts matched marker — aborting to avoid wrong-session bind"`. Do NOT pick one.

(The two `success` JSON shapes are inlined above per branch. Every success path through R4.4 MUST emit a complete 9-field JSON object — never rely on implicit defaults, never leave a field omitted, never write a literal placeholder like `"<uuid>"` in the output.)

### Step R5: Retry once on any failure (TERMINAL — main will not re-dispatch)

You have at most ONE retry per dispatch. This retry is the ONLY retry in the system — main treats your `launch_failure` result as terminal and will NOT re-dispatch you. Track the retry counter in your reasoning.

On retry, first generate a NEW `ATTEMPT_ID` (the old one stays in the old
rollout; we must not let the grep match it again). Then choose exactly one
branch:

**A. Recoverable interrupted stream (R4.0 matched).** Write
`/tmp/agy-recovery-prompt-${REVIEW_ID}.md` with the new marker first, followed
by this compact continuation prompt:

```text
<review_method>
Perform a static review only.
Continue to use only the exact read-only git diff commands from the original task plus repository file read/search and static tracing.
Do NOT execute any command whose purpose is to verify, build, or run the project.
Do not run tests, builds, compilation, linting, formatting, dependency operations, generators, migrations, project scripts, applications, services, or containers.
Do not add a Verification section or report commands/checks as if you performed them.
</review_method>

<repository_context>
Absolute repository root: ${REPO_ROOT}
Treat every relative repository path as relative to this directory.
Use absolute paths under this root for repository file reads and searches; explicitly supplied task artifacts outside the root may be read at their given paths.
Never guess that a conventional manifest or configuration file exists at the repository root. If its path was not supplied, locate it before reading it.
</repository_context>

The previous response stream was interrupted. Continue the SAME review from the evidence and task already in this conversation. Do not restart broad repository discovery. Return only the required Summary, Findings, and Verdict sections, ending with VERDICT: APPROVED or VERDICT: REVISE.
```

Launch it synchronously with the Step R3 recovery command using
`${RECOVERY_CONVERSATION_ID}`. Set `INTERRUPTED_STREAM_RECOVERY=true`, extract
the review, and run R4.0–R4.4 with the recovery-specific guards above.

**B. Every other retryable failure, including a read-only missing-file
candidate that failed its completed-turn guard.** Rewrite the operation's original prompt
file with the new marker and re-launch with the same Step R3 command as before.

Whichever branch is chosen, this is the second and final attempt. Any check's
"route to retry" outcome now becomes terminal — do NOT re-enter R5. Apply the
terminal-result rule below (timeout if exit 124 again, else launch_failure).

If the second attempt also fails any check:
- For `OPERATION=resume`: before writing the `launch_failure` result, **archive the diagnostic files** (main will need them for the fallback fresh-exec which reuses the same base paths):

```bash
mv /tmp/agy-stdout-${REVIEW_ID}.jsonl /tmp/agy-stdout-${REVIEW_ID}-failed-resume.jsonl 2>/dev/null
mv /tmp/agy-stderr-${REVIEW_ID}.txt    /tmp/agy-stderr-${REVIEW_ID}-failed-resume.txt 2>/dev/null
```

Then write the result with `archived_stdout` and `archived_stderr` set to the `-failed-resume.*` paths.

- For `OPERATION=initial` or `OPERATION=fresh-exec`: no archival needed (there is no next attempt within this REVIEW_ID to collide). Leave files at their normal paths for main's diagnostic read (main is allowed to `mv`/`rm` by path; it just doesn't read content).

Write the appropriate terminal result and return the `RUNNER_RESULT_AT: ...` line:
- Second attempt exit was 124 → write `{"result":"timeout","errors":"agy exceeded 600s on both attempts", ...}` (9 fields, all others null as applicable).
- Any other failure mode → write `launch_failure` with the failed-check diagnostic, parsed JSON `status` / `error` when available, plus the stderr tail when non-empty (combined ≤500 chars) in `errors`. Do not replace a semantic diagnostic such as conversation-id mismatch or missing verdict with an empty stderr tail.
In both cases, fill all 9 fields (set `archived_stdout`/`archived_stderr` only when the archival mv in the OPERATION=resume branch ran, else null; set `user_warning` null; set `verdict` null; set `review_file` to `/tmp/agy-review-${REVIEW_ID}.md` only if that file contains a valid VERDICT line, else null).

### Step R6: Cleanup

Do NOT delete `/tmp/agy-*` files. The main orchestrator owns the review-lifecycle cleanup at SKILL.md Step 9. Leaving files in place lets main:
- Read `review_file` after parsing your result.
- Keep the `-failed-resume.*` archives available for the fresh-exec fallback.
- Clean the whole set (including `-failed-resume.*`) when the review concludes via its existing Step 9 `rm` glob.

The one exception is the `mv` in Step R5 above — this is NOT cleanup (files are preserved, just renamed to avoid collision with the imminent fresh-exec). Doing the `mv` in the runner rather than main eliminates the isolation-claim drift that would otherwise occur if main had to touch stdout/stderr paths in its own Bash argv.

## Notes

- You run as a subagent. Your context is disposed when you return. Anything you read (stderr files, rollout paths, JSONL streams) does NOT reach the main thread — that is the whole point.
- Do NOT ask the main thread clarifying questions. If input is missing or malformed, write an `input_error` result to `${RESULT_PATH}` and return the `RUNNER_RESULT_AT:` line.
- Do NOT attempt to apply fixes, interpret severity, or re-run more than one retry. The 5-round orchestration loop lives in main.
- The final line of your message is ONLY `RUNNER_RESULT_AT: <path>` — nothing before, nothing after, no markdown fence. Main's regex tolerates minor wrapping, but adhering to the spec eliminates edge cases entirely.
