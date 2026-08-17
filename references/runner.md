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
PROMPT_BODY_PATH: <absolute path to file containing the review prompt body WITHOUT the session marker; main writes this before dispatch>
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
- `result=launch_failure` ⇒ infrastructure retry (one internal retry) already failed. Main treats this as TERMINAL — it will NOT re-dispatch you. `errors` MUST name the failed check and include the tail of stderr when stderr is non-empty.
- `result=infra_error` ⇒ something outside agy (e.g. `/tmp` not writable).
- `user_warning` is non-null when main should surface a one-line warning to the user (e.g. §2.4.4 zero-find on resume).
- Do NOT return the review text in the JSON. Main reads `review_file` directly.

## Step-by-step

### Step R1: Generate ATTEMPT_ID

Generate a fresh 6-digit random integer. Use `printf` with `$RANDOM`:

```bash
printf '%06d\n' $((RANDOM * RANDOM % 1000000))
```

Save the output as `${ATTEMPT_ID}` for this invocation. Generate a NEW ATTEMPT_ID on every retry (Step R5).

### Step R2: Build the launch prompt file

Read `${PROMPT_BODY_PATH}` (main wrote it before dispatching you).

**Fail-closed static-review policy check.** Before writing a launch prompt or
calling agy, require the body to contain exactly one `<review_method>` opening
tag, exactly one `</review_method>` closing tag, and all three literal anchors
below:

- `Perform a static review only.`
- `Do NOT execute any command whose purpose is to verify, build, or run the project.`
- `Do not add a Verification section or report commands/checks as if you performed`

If either tag or any anchor is missing, or if either tag occurs more than once,
write an `input_error` result with
`errors: "prompt body is missing or duplicates the required static-review policy"`
and return the `RUNNER_RESULT_AT:` line. Do NOT reconstruct the policy, launch
agy, or retry. This check applies to initial, fresh-exec, and resume operations.

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

Bash tool `timeout` parameter: `620000` (10 min + headroom).

For `OPERATION=resume`:

```bash
cd "${REPO_ROOT}" && timeout 600 agy --print "$(cat /tmp/agy-resume-prompt-${REVIEW_ID}.md)" \
  --conversation ${AGY_CONVERSATION_ID} \
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

**Check R4.1: Exit code.**
- `124` → route to retry (Step R5). Same retry budget as any other failure — ONE retry per dispatch total. If retry also returns 124, write `{"result":"timeout",...}` and return. (Retrying on timeout keeps the 2-attempts-per-round invariant consistent across failure types; main treats timeout as terminal just like launch_failure.)
- `≠ 0 and ≠ 124` → read `/tmp/agy-stderr-${REVIEW_ID}.txt`, route to retry (Step R5).
- `0` → proceed.

**Check R4.2: Stderr sanity.** Read `/tmp/agy-stderr-${REVIEW_ID}.txt`.
- File missing → return `{"result":"infra_error","errors":"stderr file missing — /tmp writability?",...}`.
- File contains a line matching `^Error:` or `Failed to write` → route to retry (Step R5).
- For `OPERATION=resume`, file contains a line matching `^warning: conversation .* not found$` → route to retry. agy 1.1.12 otherwise exits 0 and silently starts a new conversation.

**Check R4.3: JSON and review sanity.** First parse `/tmp/agy-stdout-${REVIEW_ID}.jsonl` as one JSON object. Parse failure or `status != "SUCCESS"` → route to retry. (The `.jsonl` suffix is historical; agy `--output-format json` emits one object.) For `OPERATION=resume`, also require a valid `conversation_id` equal to the input `${AGY_CONVERSATION_ID}` before inspecting the verdict; a missing or different id routes to retry with `errors: "resume conversation id changed: requested <input>, got <captured-or-missing>"`. This check MUST run even when the response says `VERDICT: APPROVED`.

Only after the JSON checks pass, Read `/tmp/agy-review-${REVIEW_ID}.md`.
- File missing or empty → route to retry (Step R5).
- Does NOT contain a line matching `^VERDICT: (APPROVED|REVISE)$` → route to retry.
- Verdict is `REVISE` AND file contains NO line matching `\[severity:\s*(critical|high|medium)` → route to retry (reviewer format drift).
- Verdict is `APPROVED` → write this EXACT JSON object to `${RESULT_PATH}` and return the `RUNNER_RESULT_AT:` line:

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

On retry:
1. Generate a NEW `ATTEMPT_ID` (the old one stays in the old rollout; we must not let the grep match it again).
2. Rewrite the prompt file with the new marker (using the Write tool; the write itself bumps mtime — do NOT use Bash `touch`, which may be gated by inherited Plan Mode on the subagent).
3. Re-launch (same Step R3 command, still `run_in_background: false`).
4. Re-run checks R4.1–R4.4. **This is the second and final attempt.** On this re-run, any check's "route to retry" outcome becomes terminal — do NOT re-enter R5. Apply the terminal-result rule below (timeout if exit 124 again, else launch_failure).

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
- Any other failure mode → write `launch_failure` with the failed-check diagnostic plus the stderr tail when non-empty (combined ≤500 chars) in `errors`. Do not replace a semantic diagnostic such as conversation-id mismatch or missing verdict with an empty stderr tail.
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
