#!/usr/bin/env python3
"""Create secret-safe Gitleaks reports and GitHub annotations.

The input report is treated as sensitive. Output files intentionally omit
matched text, source lines, secret values, authors, email addresses, and commit
messages.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


SENSITIVE_FIELDS = {
    "Secret",
    "Match",
    "Line",
    "Author",
    "Email",
    "Message",
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render redacted reports from a Gitleaks JSON report."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--scan-exit-code", type=int, required=True)
    parser.add_argument("--gitleaks-version", required=True)
    return parser.parse_args()


def safe_string(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return "".join(
        character for character in text if character in "\t\n\r" or ord(character) >= 32
    )


def safe_integer(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def sanitize_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Retain only metadata that cannot reveal the detected secret."""

    return {
        "rule_id": safe_string(finding.get("RuleID")),
        "file": safe_string(finding.get("File")),
        "start_line": safe_integer(finding.get("StartLine")),
        "end_line": safe_integer(finding.get("EndLine")),
        "commit": safe_string(finding.get("Commit")),
        "date": safe_string(finding.get("Date")),
        "fingerprint": safe_string(finding.get("Fingerprint")),
        "tags": [
            safe_string(tag) for tag in finding.get("Tags", []) if safe_string(tag)
        ],
    }


def load_findings(report_path: Path) -> list[dict[str, Any]]:
    if not report_path.exists():
        raise ValueError("Gitleaks did not create its JSON report.")

    content = report_path.read_text(encoding="utf-8").strip()
    if not content:
        return []

    parsed = json.loads(content)
    if not isinstance(parsed, list):
        raise ValueError("The Gitleaks JSON report is not an array.")

    findings: list[dict[str, Any]] = []
    for index, finding in enumerate(parsed):
        if not isinstance(finding, dict):
            raise ValueError(f"Gitleaks report entry {index} is not an object.")
        findings.append(sanitize_finding(finding))

    return sorted(
        findings,
        key=lambda item: (
            item["file"].casefold(),
            item["start_line"],
            item["rule_id"].casefold(),
            item["commit"],
        ),
    )


def determine_status(scan_exit_code: int, finding_count: int) -> str:
    if scan_exit_code == 0 and finding_count == 0:
        return "clean"
    if scan_exit_code == 1 and finding_count > 0:
        return "findings"
    return "scanner-error"


def markdown_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("`", r"\`")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def command_data_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def command_property_escape(value: str) -> str:
    return command_data_escape(value).replace(":", "%3A").replace(",", "%2C")


def emit_annotations(findings: list[dict[str, Any]]) -> None:
    for finding in findings:
        file_path = finding["file"]
        line = finding["start_line"] or 1
        rule_id = finding["rule_id"] or "unknown-rule"
        commit = finding["commit"][:12] or "unknown commit"

        message = (
            f"Potential secret detected by Gitleaks rule "
            f"'{rule_id}' in commit {commit}. "
            "The secret value is intentionally omitted."
        )

        properties = [
            f"file={command_property_escape(file_path)}",
            f"line={line}",
            "title=Gitleaks secret detection",
        ]
        print(f"::error {','.join(properties)}::{command_data_escape(message)}")


def render_markdown(
    version: str,
    status: str,
    scan_exit_code: int,
    findings: list[dict[str, Any]],
    error_message: str | None = None,
) -> str:
    lines = [
        "## Secret scanning",
        "",
        f"- **Gitleaks version:** `{markdown_escape(version)}`",
        "- **Scan scope:** Complete Git history (`--all`)",
        "- **Secret redaction:** 100%",
        f"- **Scanner exit code:** `{scan_exit_code}`",
        f"- **Potential secrets detected:** {len(findings)}",
        "",
    ]

    if status == "clean":
        lines.append("✅ No potential secrets were detected.")
    elif status == "findings":
        lines.extend(
            [
                "❌ Potential secrets were detected.",
                "",
                "The matched values are intentionally omitted. Rotate or revoke "
                "any real credentials before changing Git history or adding an "
                "ignore fingerprint.",
                "",
                "| File | Line | Rule | Commit | Fingerprint |",
                "| --- | ---: | --- | --- | --- |",
            ]
        )
        for finding in findings:
            commit = finding["commit"]
            lines.append(
                "| "
                f"`{markdown_escape(finding['file'])}` | "
                f"{finding['start_line'] or '-'} | "
                f"`{markdown_escape(finding['rule_id'] or 'unknown-rule')}` | "
                f"`{markdown_escape(commit[:12] or 'unknown')}` | "
                f"`{markdown_escape(finding['fingerprint'] or 'unavailable')}` |"
            )
    else:
        lines.append("❌ Gitleaks could not produce a consistent scan result.")
        if error_message:
            lines.extend(
                [
                    "",
                    "```text",
                    error_message.replace("```", r"\`\`\`"),
                    "```",
                ]
            )

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(status: str, finding_count: int) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return

    with Path(output_path).open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"status={status}\n")
        output.write(f"finding-count={finding_count}\n")


def append_summary(markdown: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    with Path(summary_path).open("a", encoding="utf-8", newline="\n") as summary:
        summary.write(markdown)


def assert_no_sensitive_fields(
    sanitized_findings: list[dict[str, Any]],
) -> None:
    serialized = json.dumps(sanitized_findings)
    for field in SENSITIVE_FIELDS:
        if f'"{field}"' in serialized:
            raise AssertionError(f"Sensitive field unexpectedly present: {field}")


def main() -> int:
    arguments = parse_arguments()

    status = "scanner-error"
    findings: list[dict[str, Any]] = []
    error_message: str | None = None

    try:
        findings = load_findings(arguments.input)
        assert_no_sensitive_fields(findings)
        status = determine_status(arguments.scan_exit_code, len(findings))
        if status == "scanner-error":
            error_message = (
                "The scanner exit code and report contents are inconsistent. "
                "This can indicate a Gitleaks execution or configuration error."
            )
    except (OSError, ValueError, json.JSONDecodeError, AssertionError) as error:
        error_message = safe_string(error)

    arguments.json_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.json_output.write_text(
        json.dumps(findings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    markdown = render_markdown(
        version=arguments.gitleaks_version,
        status=status,
        scan_exit_code=arguments.scan_exit_code,
        findings=findings,
        error_message=error_message,
    )
    arguments.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.markdown_output.write_text(
        markdown,
        encoding="utf-8",
        newline="\n",
    )

    if status == "findings":
        emit_annotations(findings)

    append_summary(markdown)
    write_outputs(status, len(findings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
