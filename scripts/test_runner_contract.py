#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import tempfile
import unittest

from runner_contract import RECOVERED_READ_WARNING, rate_limit_reason, validate_prompt, validate_recovered_read


ROOT = "/work/repo"
CONVERSATION = "12345678-1234-4234-9234-123456789abc"
MARKER = "ADVERSARIAL-REVIEW-SESSION: 1700000000-12345678-654321"
REVIEW_ID = "1700000000-12345678"
REVIEW = "Summary\n\nFindings\n\nNo findings.\n\nVERDICT: APPROVED\n"


def prompt(history: str = "") -> str:
    suffix = f"\n## Previous review rounds\n{history}" if history else ""
    return f"""<review_method>
Perform a static review only.
Do NOT execute any command whose purpose is to verify, build, or run the project.
Do not add a Verification section or report commands/checks as if you performed them.
Every `find_by_name` call MUST include a non-empty `Pattern`; use `Pattern: "*"` to enumerate a directory.
</review_method>
<repository_context>
Absolute repository root: {ROOT}
</repository_context>
<!-- ADVERSARIAL-REVIEW-CONTRACT: {REVIEW_ID} -->
Review the supplied diff.{suffix}
"""


class RateLimitTests(unittest.TestCase):
    def test_first_quota_error_is_terminal_despite_retryable_flag(self) -> None:
        stderr = json.dumps({"status": "RESOURCE_EXHAUSTED", "error_code": 429, "retryable": True})
        stdout = json.dumps({"status": "ERROR", "error": "Individual quota reached. Resets in 146h17m43s."})
        self.assertIsNotNone(rate_limit_reason(stdout, stderr))

    def test_structured_and_plain_http_errors(self) -> None:
        for output in ('{"status":"ERROR","error":{"code":429}}',
                       '{"status":"ERROR","status_code":"429"}',
                       'HTTP 429 Too Many Requests',
                       '{"status":"RESOURCE_EXHAUSTED"}'):
            with self.subTest(output=output):
                self.assertIsNotNone(rate_limit_reason(output, ""))
        self.assertIsNotNone(rate_limit_reason("", "Error: HTTP 429"))

    def test_review_content_is_not_a_rate_limit(self) -> None:
        for status in ("SUCCESS", "ERROR"):
            output = json.dumps({"status": status, "error": "unrelated stream failure" if status == "ERROR" else None,
                                 "response": "Handle HTTP 429 and RESOURCE_EXHAUSTED in the API. VERDICT: REVISE"})
            self.assertIsNone(rate_limit_reason(output, ""))

    def test_other_failures_remain_eligible_for_existing_retry(self) -> None:
        for error in ("HTTP 503", "connection reset", "Agent execution terminated due to error.", "1429 records"):
            self.assertIsNone(rate_limit_reason(json.dumps({"status": "ERROR", "error": error}), ""))


class PromptContractTests(unittest.TestCase):
    def _code_template(self) -> str:
        skill = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
        template = skill.split("**Prompt body for code review (≤ 50 files):**", 1)[1].split("```", 2)[1]
        return template.replace("<repo-root>", ROOT).replace("<review-id>", REVIEW_ID)

    def test_standalone_task_replacement_preserves_trusted_policy(self) -> None:
        body = self._code_template()
        boundary = f"<!-- ADVERSARIAL-REVIEW-CONTRACT: {REVIEW_ID} -->"
        original_prefix = body.split(boundary, 1)[0]
        task_block = "<task>\nReview these changes; keep literal \\1 and inline <task>.\n</task>"
        pattern = re.compile(r"^<task>[ \t]*\r?\n.*?^</task>[ \t]*$", re.MULTILINE | re.DOTALL)
        replaced, count = pattern.subn(lambda _: task_block, body)
        self.assertEqual(count, 1)
        self.assertEqual(replaced.split(boundary, 1)[0], original_prefix)
        self.assertIn(task_block, replaced)
        valid, reason = validate_prompt(replaced, ROOT, "initial", REVIEW_ID)
        self.assertTrue(valid, reason)

    def test_unanchored_task_replacement_is_rejected_before_launch(self) -> None:
        broken = re.sub(r"<task>.*?</task>", "<task>Review changes.</task>", self._code_template(), flags=re.DOTALL)
        valid, reason = validate_prompt(broken, ROOT, "initial", REVIEW_ID)
        self.assertFalse(valid)
        self.assertIn("contract boundary", reason)

    def test_history_may_quote_contract_tags(self) -> None:
        valid, reason = validate_prompt(
            prompt("quoted <repository_context> and <review_method>"),
            ROOT,
            "fresh-exec",
            REVIEW_ID,
        )
        self.assertTrue(valid, reason)

    def test_history_may_quote_contract_boundary(self) -> None:
        boundary = f"<!-- ADVERSARIAL-REVIEW-CONTRACT: {REVIEW_ID} -->"
        valid, reason = validate_prompt(prompt(f"quoted {boundary}"), ROOT, "fresh-exec", REVIEW_ID)
        self.assertTrue(valid, reason)

    def test_resume_payload_may_quote_contract_tags(self) -> None:
        body = prompt() + "quoted <repository_context> and <review_method>"
        valid, reason = validate_prompt(
            body, ROOT, "resume", REVIEW_ID
        )
        self.assertTrue(valid, reason)

    def test_duplicate_trusted_tag_is_rejected(self) -> None:
        body = prompt().replace("<repository_context>", "<repository_context>\n<repository_context>")
        valid, _ = validate_prompt(body, ROOT, "initial", REVIEW_ID)
        self.assertFalse(valid)

    def test_wrong_root_prefix_is_rejected(self) -> None:
        body = prompt().replace(
            f"Absolute repository root: {ROOT}",
            f"Absolute repository root: {ROOT}sitory",
        )
        valid, _ = validate_prompt(body, ROOT, "initial", REVIEW_ID)
        self.assertFalse(valid)

    def test_missing_find_by_name_contract_is_rejected(self) -> None:
        body = prompt().replace(
            'Every `find_by_name` call MUST include a non-empty `Pattern`; use `Pattern: "*"` to enumerate a directory.\n',
            "",
        )
        valid, _ = validate_prompt(body, ROOT, "initial", REVIEW_ID)
        self.assertFalse(valid)


class RecoveredReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.stdout = self.base / "stdout.json"
        self.transcript = self.base / "transcript.jsonl"
        self.body = self.base / "body.md"
        self.original_body = self.base / "original-body.md"
        self.stderr = self.base / "stderr.txt"
        self.missing = f"{ROOT}/package.json"
        self.replacement = f"{ROOT}/src/main/frontend/package.json"
        self.body.write_text(prompt(), encoding="utf-8")
        self.original_body.write_text(prompt(), encoding="utf-8")
        self.stderr.write_text("", encoding="utf-8")
        self._write_fixture()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _envelope(self, missing: str | None = None, response: str = REVIEW) -> dict[str, object]:
        failed = missing or self.missing
        return {
            "status": "ERROR",
            "error": (
                "declaring permissions: cortex tool view_file: convert tool call for permissions: "
                "model output error: invalid tool call error (invalid_args) failed to read file: open "
                f"{failed}: no such file or directory"
            ),
            "response": response,
            "conversation_id": CONVERSATION,
        }

    def _records(self, final: str = REVIEW) -> list[dict[str, object]]:
        return [
            {"type": "USER_INPUT", "content": MARKER},
            {
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": self.missing}}],
            },
            {
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": self.replacement}}],
            },
            {"type": "GENERIC", "status": "DONE", "content": "package contents"},
            {"type": "PLANNER_RESPONSE", "status": "DONE", "content": final},
        ]

    def _write_fixture(
        self,
        *,
        missing: str | None = None,
        records: list[dict[str, object]] | None = None,
        response: str = REVIEW,
    ) -> None:
        self.stdout.write_text(json.dumps(self._envelope(missing, response)), encoding="utf-8")
        rows = records if records is not None else self._records(response)
        self.transcript.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    def _args(self, exit_code: int = 0) -> argparse.Namespace:
        return argparse.Namespace(
            agy_exit_code=exit_code,
            stdout_json=str(self.stdout),
            transcript=str(self.transcript),
            prompt_body=str(self.body),
            original_prompt_body=str(self.original_body),
            repo_root=ROOT,
            marker=MARKER,
            operation="initial",
            expected_conversation_id=None,
            stderr=str(self.stderr),
        )

    def _valid(self, exit_code: int = 0) -> tuple[bool, str, str | None]:
        return validate_recovered_read(self._args(exit_code))

    def test_accepts_proven_auxiliary_recovery(self) -> None:
        valid, reason, warning = self._valid()
        self.assertTrue(valid, reason)
        self.assertEqual(warning, RECOVERED_READ_WARNING)

    def test_rejects_nonzero_exit(self) -> None:
        self.assertFalse(self._valid(1)[0])

    def test_rejects_parent_traversal(self) -> None:
        traversal = f"{ROOT}/../outside/package.json"
        self._write_fixture(missing=traversal)
        self.assertFalse(self._valid()[0])

    def test_rejects_outside_root(self) -> None:
        self._write_fixture(missing="/outside/package.json")
        self.assertFalse(self._valid()[0])

    def test_rejects_symlink_escape(self) -> None:
        repo = self.base / "repo"
        outside = self.base / "outside"
        repo.mkdir()
        outside.mkdir()
        (repo / "link").symlink_to(outside, target_is_directory=True)
        self._write_fixture(missing=str(repo / "link" / "package.json"))
        args = self._args()
        args.repo_root = str(repo)
        self.assertFalse(validate_recovered_read(args)[0])

    def test_rejects_missing_required_evidence(self) -> None:
        self.body.write_text(prompt() + f"Required artifact: {self.missing}\n", encoding="utf-8")
        self.assertFalse(self._valid()[0])

    def test_resume_rejects_path_supplied_only_by_original_task(self) -> None:
        self.body.write_text(prompt(), encoding="utf-8")
        self.original_body.write_text(
            prompt() + f"Required artifact: {self.missing}\n", encoding="utf-8"
        )
        args = self._args()
        args.operation = "resume"
        args.expected_conversation_id = CONVERSATION
        self.assertFalse(validate_recovered_read(args)[0])

    def test_rejects_no_same_filename_recovery(self) -> None:
        records = self._records()
        records[2] = {
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": f"{ROOT}/README.md"}}],
        }
        self._write_fixture(records=records)
        self.assertFalse(self._valid()[0])

    def test_rejects_same_filename_call_without_result(self) -> None:
        records = self._records()
        del records[3]
        self._write_fixture(records=records)
        self.assertFalse(self._valid()[0])

    def test_rejects_narrative_only_view_file_spoof(self) -> None:
        records = self._records()
        records[2] = {
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "content": f"I used view_file on {self.replacement}",
        }
        self._write_fixture(records=records)
        self.assertFalse(self._valid()[0])

    def test_rejects_result_belonging_to_another_tool(self) -> None:
        records = self._records()
        records.insert(
            3,
            {
                "type": "PLANNER_RESPONSE",
                "status": "DONE",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pwd"}}],
            },
        )
        self._write_fixture(records=records)
        self.assertFalse(self._valid()[0])

    def test_rejects_mixed_tool_call_record(self) -> None:
        records = self._records()
        records[2]["tool_calls"] = [
            {"name": "run_command", "args": {"CommandLine": "pwd"}},
            {"name": "view_file", "args": {"AbsolutePath": self.replacement}},
        ]
        self._write_fixture(records=records)
        self.assertFalse(self._valid()[0])

    def test_rejects_transcript_error(self) -> None:
        records = self._records()
        records.insert(4, {"type": "ERROR_MESSAGE", "content": "boom"})
        self._write_fixture(records=records)
        self.assertFalse(self._valid()[0])

    def test_rejects_response_mismatch(self) -> None:
        self._write_fixture(records=self._records("different"))
        self.assertFalse(self._valid()[0])


if __name__ == "__main__":
    unittest.main()
