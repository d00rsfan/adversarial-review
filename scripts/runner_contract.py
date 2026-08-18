#!/usr/bin/env python3
"""Deterministic validation for adversarial-review runner edge cases."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import uuid
from typing import Any


READ_ERROR_PREFIX = (
    "declaring permissions: cortex tool view_file: convert tool call for "
    "permissions: model output error: invalid tool call error (invalid_args) "
    "failed to read file: open "
)
READ_ERROR_SUFFIX = ": no such file or directory"
RECOVERED_READ_WARNING = (
    "agy completed the marker-bound review after a recoverable read-only "
    "missing-file error; the transcript proves a later successful repository-local "
    "read of the same filename and the completed response exactly matches the review"
)
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
SEVERITY_RE = re.compile(r"\[severity:\s*(critical|high|medium)", re.IGNORECASE)
VERDICT_RE = re.compile(r"^VERDICT: (APPROVED|REVISE)$", re.MULTILINE)
CONTRACT_MARKER = "<!-- ADVERSARIAL-REVIEW-CONTRACT: {review_id} -->"


def _emit(valid: bool, reason: str, warning: str | None = None) -> int:
    print(json.dumps({"valid": valid, "reason": reason, "warning": warning}))
    return 0 if valid else 1


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _trusted_prompt_prefix(body: str, review_id: str) -> str:
    contract_boundary = CONTRACT_MARKER.format(review_id=review_id)
    boundary_index = body.find(contract_boundary)
    if boundary_index < 0:
        raise ValueError("prompt is missing its review-scoped contract boundary")
    trusted = body[:boundary_index]
    return trusted


def validate_prompt(
    body: str, repo_root: str, operation: str, review_id: str
) -> tuple[bool, str]:
    try:
        trusted = _trusted_prompt_prefix(body, review_id)
    except ValueError as exc:
        return False, str(exc)
    required = (
        "Perform a static review only.",
        "Do NOT execute any command whose purpose is to verify, build, or run the project.",
        "Do not add a Verification section or report commands/checks as if you performed",
    )
    if trusted.count("<review_method>") != 1 or trusted.count("</review_method>") != 1:
        return False, "trusted prompt header is missing or duplicates the static-review policy"
    if any(anchor not in trusted for anchor in required):
        return False, "trusted prompt header is missing a static-review policy anchor"
    if trusted.count("<repository_context>") != 1 or trusted.count("</repository_context>") != 1:
        return False, "trusted prompt header is missing or duplicates repository context"
    root_line = f"Absolute repository root: {repo_root}"
    if sum(line.rstrip("\r") == root_line for line in trusted.splitlines()) != 1:
        return False, "trusted prompt header repository root is missing, duplicated, or mismatched"
    return True, "prompt contract satisfied"


def _valid_uuid(value: Any) -> bool:
    if not isinstance(value, str) or not UUID_RE.fullmatch(value):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _load_records(path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(_read_text(path).splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"transcript line {number} is not an object")
        records.append(value)
    return records


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for key, child in value.items():
            result.append(str(key))
            result.extend(_flatten_strings(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(_flatten_strings(child))
        return result
    return []


def _record_text(record: dict[str, Any]) -> str:
    return "\n".join(_flatten_strings(record))


def _record_type(record: dict[str, Any]) -> str:
    value = record.get("type")
    return value.upper() if isinstance(value, str) else ""


def _repo_local(path: str, repo_root: str) -> tuple[bool, str | None]:
    if not os.path.isabs(path):
        return False, None
    if any(part in {".", ".."} for part in PurePosixPath(path).parts):
        return False, None
    root_real = os.path.realpath(repo_root)
    path_real = os.path.realpath(path)
    try:
        inside = os.path.commonpath((root_real, path_real)) == root_real
    except ValueError:
        return False, None
    if not inside or path_real == root_real:
        return False, None
    return True, path_real


def _view_file_paths(record: dict[str, Any]) -> list[str]:
    if _record_type(record) != "PLANNER_RESPONSE" or record.get("status") != "DONE":
        return []
    tool_calls = record.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        return []
    call = tool_calls[0]
    if not isinstance(call, dict) or call.get("name") != "view_file":
        return []
    arguments = call.get("args")
    if not isinstance(arguments, dict):
        return []
    absolute_path = arguments.get("AbsolutePath")
    return [absolute_path] if isinstance(absolute_path, str) else []


def _has_successful_tool_result(records: list[dict[str, Any]], call_index: int) -> bool:
    if call_index + 1 >= len(records):
        return False
    result = records[call_index + 1]
    if _record_type(result) != "GENERIC":
        return False
    status = result.get("status")
    content = result.get("content")
    return (
        isinstance(status, str)
        and status == "DONE"
        and isinstance(content, str)
        and bool(content.strip())
    )


def _body_supplies_path(body: str, missing_path: str, repo_root: str) -> bool:
    relative = os.path.relpath(missing_path, repo_root)
    return missing_path in body or relative in body


def validate_recovered_read(args: argparse.Namespace) -> tuple[bool, str, str | None]:
    if args.agy_exit_code != 0:
        return False, "recoverable read exception requires agy exit code 0", None
    try:
        envelope = json.loads(_read_text(args.stdout_json))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"diagnostic JSON is unreadable: {exc}", None
    if not isinstance(envelope, dict):
        return False, "diagnostic JSON is not an object", None
    if envelope.get("status") != "ERROR":
        return False, "diagnostic status is not ERROR", None
    response = envelope.get("response")
    if not isinstance(response, str) or not response.strip():
        return False, "diagnostic response is empty", None
    conversation_id = envelope.get("conversation_id")
    if not _valid_uuid(conversation_id):
        return False, "diagnostic conversation_id is not a canonical UUID", None
    if args.operation == "resume" and conversation_id != args.expected_conversation_id:
        return False, "resume conversation id changed", None
    if args.operation == "resume" and args.stderr:
        stderr = _read_text(args.stderr)
        if re.search(r"^warning: conversation .* not found$", stderr, re.MULTILINE):
            return False, "resume conversation was not found", None

    error = envelope.get("error")
    if not isinstance(error, str) or not error.startswith(READ_ERROR_PREFIX) or not error.endswith(READ_ERROR_SUFFIX):
        return False, "diagnostic error is not the allowlisted view_file ENOENT signature", None
    missing_path = error[len(READ_ERROR_PREFIX) : -len(READ_ERROR_SUFFIX)]
    local, missing_real = _repo_local(missing_path, args.repo_root)
    if not local or missing_real is None:
        return False, "missing path is not canonically contained in the repository", None
    try:
        body = _read_text(args.prompt_body)
        original_body = _read_text(args.original_prompt_body)
    except OSError as exc:
        return False, f"current or original prompt body is unreadable: {exc}", None
    if _body_supplies_path(body, missing_path, args.repo_root) or _body_supplies_path(
        original_body, missing_path, args.repo_root
    ):
        return False, "missing path was supplied by the review task and is not auxiliary", None

    verdict = VERDICT_RE.search(response)
    if verdict is None:
        return False, "response has no valid verdict", None
    if verdict.group(1) == "REVISE" and not SEVERITY_RE.search(response):
        return False, "REVISE response has no actionable severity", None

    try:
        records = _load_records(args.transcript)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"transcript is unreadable: {exc}", None
    marker_indexes = [
        index
        for index, record in enumerate(records)
        if _record_type(record) == "USER_INPUT" and args.marker in _record_text(record)
    ]
    if not marker_indexes:
        return False, "transcript does not contain the marker-bound USER_INPUT", None
    subsequent = records[marker_indexes[-1] + 1 :]
    if any(_record_type(record) == "ERROR_MESSAGE" for record in subsequent):
        return False, "marker-bound transcript contains ERROR_MESSAGE", None

    failed_indexes = [
        index
        for index, record in enumerate(subsequent)
        if missing_path in _view_file_paths(record)
    ]
    if not failed_indexes:
        return False, "transcript does not contain the failed view_file call", None
    failed_index = failed_indexes[-1]
    basename = os.path.basename(missing_path)
    recovered = False
    for index, record in enumerate(subsequent[failed_index + 1 :], start=failed_index + 1):
        if not _has_successful_tool_result(subsequent, index):
            continue
        for candidate in _view_file_paths(record):
            if os.path.basename(candidate) != basename:
                continue
            candidate_local, candidate_real = _repo_local(candidate, args.repo_root)
            if candidate_local and candidate_real != missing_real:
                recovered = True
                break
        if recovered:
            break
    if not recovered:
        return False, "transcript has no later successful repository-local view_file of the same filename", None

    planner = [record for record in subsequent if _record_type(record) == "PLANNER_RESPONSE"]
    if not planner or planner[-1].get("status") != "DONE":
        return False, "final marker-bound planner response is not DONE", None
    content = planner[-1].get("content")
    if not isinstance(content, str) or content.rstrip("\r\n") != response.rstrip("\r\n"):
        return False, "final marker-bound planner response does not match diagnostic response", None
    return True, "recovered read contract satisfied", RECOVERED_READ_WARNING


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prompt = subparsers.add_parser("prompt")
    prompt.add_argument("--prompt-body", required=True)
    prompt.add_argument("--repo-root", required=True)
    prompt.add_argument("--operation", choices=("initial", "resume", "fresh-exec"), required=True)
    prompt.add_argument("--review-id", required=True)

    recovered = subparsers.add_parser("recovered-read")
    recovered.add_argument("--stdout-json", required=True)
    recovered.add_argument("--transcript", required=True)
    recovered.add_argument("--prompt-body", required=True)
    recovered.add_argument("--original-prompt-body", required=True)
    recovered.add_argument("--repo-root", required=True)
    recovered.add_argument("--marker", required=True)
    recovered.add_argument("--operation", choices=("initial", "resume", "fresh-exec"), required=True)
    recovered.add_argument("--agy-exit-code", type=int, required=True)
    recovered.add_argument("--expected-conversation-id")
    recovered.add_argument("--stderr")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "prompt":
        try:
            body = _read_text(args.prompt_body)
        except OSError as exc:
            return _emit(False, f"prompt body is unreadable: {exc}")
        valid, reason = validate_prompt(body, args.repo_root, args.operation, args.review_id)
        return _emit(valid, reason)
    valid, reason, warning = validate_recovered_read(args)
    return _emit(valid, reason, warning)


if __name__ == "__main__":
    sys.exit(main())
