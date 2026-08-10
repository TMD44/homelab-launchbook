#!/usr/bin/env python3
"""Validate Docker Compose and matching environment configurations.

Matching `<name>.env` files receive lightweight linting before Docker Compose
validates the resolved configuration with `docker compose config --quiet`. The
validator never pulls images, builds services, starts containers, or modifies
repository configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_REPOSITORY_ROOT = SCRIPT_PATH.parents[3]
DEFAULT_APPLICATION_LIBRARY = Path("Application Library")
DEFAULT_REPORT_PATH = Path("compose-validation-report.md")
COMPOSE_PATTERNS = ("*-compose.yaml", "*-compose.yml")
COMMAND_TIMEOUT_SECONDS = 60
ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
ENV_VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class EnvironmentValidationResult:
    """Result of linting one service-specific environment file."""

    environment_file: Path
    diagnostics: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.diagnostics


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating one Compose file."""

    compose_file: Path
    environment_files: tuple[Path, ...]
    environment_validation: EnvironmentValidationResult | None
    return_code: int
    diagnostics: str

    @property
    def is_valid(self) -> bool:
        environment_is_valid = (
            self.environment_validation is None or self.environment_validation.is_valid
        )
        return environment_is_valid and self.return_code == 0

    @property
    def has_warnings(self) -> bool:
        return self.is_valid and bool(self.diagnostics)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Docker Compose files under the repository's Application Library."
        )
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=DEFAULT_REPOSITORY_ROOT,
        help="Repository root. Defaults to the root containing .github.",
    )
    parser.add_argument(
        "--application-library",
        type=Path,
        default=DEFAULT_APPLICATION_LIBRARY,
        help=(
            "Application Library path, relative to the repository root unless "
            "an absolute path is supplied."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=(
            "Markdown report path, relative to the repository root unless an "
            "absolute path is supplied."
        ),
    )
    return parser.parse_args()


def resolve_inside_repository(repository_root: Path, path: Path) -> Path:
    """Resolve a path and require it to remain inside the repository."""

    resolved = (
        path.resolve() if path.is_absolute() else (repository_root / path).resolve()
    )
    if not resolved.is_relative_to(repository_root):
        raise ValueError(f"Path escapes the repository root: {path}")
    return resolved


def discover_compose_files(application_library: Path) -> list[Path]:
    """Return every Compose file matching the repository naming convention."""

    discovered: set[Path] = set()
    for pattern in COMPOSE_PATTERNS:
        discovered.update(
            path.resolve()
            for path in application_library.rglob(pattern)
            if path.is_file()
        )
    return sorted(discovered, key=lambda path: path.as_posix().lower())


def compose_basename(compose_file: Path) -> str:
    """Return the service-specific prefix from `<name>-compose.yaml|yml`."""

    name = compose_file.name
    for suffix in ("-compose.yaml", "-compose.yml"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    raise ValueError(f"Unsupported Compose filename: {name}")


def discover_service_env_file(compose_file: Path) -> Path | None:
    """Return the optional `<compose-prefix>.env` file when it exists."""

    candidate = compose_file.parent / f"{compose_basename(compose_file)}.env"
    return candidate.resolve() if candidate.is_file() else None


def validate_environment_file(
    environment_file: Path,
) -> EnvironmentValidationResult:
    """Lint a service-specific environment file without parsing its values."""

    try:
        content = environment_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        return EnvironmentValidationResult(
            environment_file=environment_file,
            diagnostics=(f"Environment file is not valid UTF-8: {error}",),
        )
    except OSError as error:
        return EnvironmentValidationResult(
            environment_file=environment_file,
            diagnostics=(f"Unable to read environment file: {error}",),
        )

    diagnostics: list[str] = []
    first_definition_lines: dict[str, int] = {}
    in_multiline_single_quote = False

    def has_unescaped_single_quote(value: str) -> bool:
        for index, character in enumerate(value):
            if character != "'":
                continue

            backslash_count = 0
            previous_index = index - 1
            while previous_index >= 0 and value[previous_index] == "\\":
                backslash_count += 1
                previous_index -= 1

            if backslash_count % 2 == 0:
                return True

        return False

    for line_number, line in enumerate(content.splitlines(), start=1):
        if in_multiline_single_quote:
            if has_unescaped_single_quote(line):
                in_multiline_single_quote = False
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()

        delimiter_positions = [
            position
            for position in (stripped.find("="), stripped.find(":"))
            if position >= 0
        ]
        if delimiter_positions:
            delimiter_position = min(delimiter_positions)
            variable_name = stripped[:delimiter_position].strip()
            value = stripped[delimiter_position + 1 :].lstrip()
        else:
            variable_name = re.split(r"\s+#", stripped, maxsplit=1)[0].strip()
            value = None

        if not ENV_VARIABLE_NAME_PATTERN.fullmatch(variable_name):
            diagnostics.append(
                f"Line {line_number}: invalid environment variable name "
                f"`{variable_name or '<empty>'}`."
            )
            continue

        first_line = first_definition_lines.get(variable_name)
        if first_line is not None:
            diagnostics.append(
                f"Line {line_number}: duplicate environment variable "
                f"`{variable_name}` (first defined on line {first_line})."
            )
            continue

        first_definition_lines[variable_name] = line_number

        if value is not None and value.startswith("'"):
            in_multiline_single_quote = not has_unescaped_single_quote(value[1:])

    return EnvironmentValidationResult(
        environment_file=environment_file,
        diagnostics=tuple(diagnostics),
    )


def discover_interpolation_env_files(compose_file: Path) -> tuple[Path, ...]:
    """Find interpolation env files associated with a Compose file.

    `.env` is loaded first, followed by `<compose-prefix>.env`, so the
    service-specific file takes precedence when both exist.
    """

    service_directory = compose_file.parent
    candidates = (
        service_directory / ".env",
        service_directory / f"{compose_basename(compose_file)}.env",
    )

    discovered: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if candidate.is_file() and resolved not in discovered:
            discovered.append(resolved)

    return tuple(discovered)


def safe_project_name(repository_root: Path, compose_file: Path) -> str:
    """Create a deterministic Compose project name accepted by the CLI."""

    relative_path = compose_file.relative_to(repository_root).as_posix()
    readable = re.sub(r"[^a-z0-9_-]+", "-", compose_basename(compose_file).lower())
    readable = readable.strip("-_") or "compose"
    digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:10]
    return f"validation-{readable[:35]}-{digest}"


def normalize_diagnostics(output: str) -> str:
    """Remove ANSI control sequences and normalize diagnostic whitespace."""

    cleaned = ANSI_ESCAPE_PATTERN.sub("", output)
    return "\n".join(line.rstrip() for line in cleaned.strip().splitlines())


def validate_compose_file(
    repository_root: Path,
    compose_file: Path,
    environment_files: Sequence[Path],
    environment_validation: EnvironmentValidationResult | None,
) -> ValidationResult:
    """Run Docker Compose's configuration validator for one file."""

    command = [
        "docker",
        "compose",
        "--ansi",
        "never",
        "--project-name",
        safe_project_name(repository_root, compose_file),
        "--project-directory",
        str(compose_file.parent),
        "--profile",
        "*",
    ]

    for environment_file in environment_files:
        command.extend(("--env-file", str(environment_file)))

    command.extend(("--file", str(compose_file), "config", "--quiet"))

    try:
        completed = subprocess.run(
            command,
            cwd=compose_file.parent,
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        combined_output = "\n".join(
            output for output in (completed.stdout, completed.stderr) if output
        )
        diagnostics = normalize_diagnostics(combined_output)
        return ValidationResult(
            compose_file=compose_file,
            environment_files=tuple(environment_files),
            environment_validation=environment_validation,
            return_code=completed.returncode,
            diagnostics=diagnostics,
        )
    except subprocess.TimeoutExpired:
        return ValidationResult(
            compose_file=compose_file,
            environment_files=tuple(environment_files),
            environment_validation=environment_validation,
            return_code=124,
            diagnostics=(
                f"Docker Compose validation exceeded {COMMAND_TIMEOUT_SECONDS} seconds."
            ),
        )
    except OSError as error:
        return ValidationResult(
            compose_file=compose_file,
            environment_files=tuple(environment_files),
            environment_validation=environment_validation,
            return_code=127,
            diagnostics=f"Unable to execute Docker Compose: {error}",
        )


def get_compose_version() -> str:
    """Return the installed Docker Compose CLI version."""

    try:
        completed = subprocess.run(
            ["docker", "compose", "version", "--short"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"unavailable ({error})"

    version = normalize_diagnostics(
        "\n".join(output for output in (completed.stdout, completed.stderr) if output)
    )
    return version or "unknown"


def markdown_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def display_path(repository_root: Path, path: Path) -> str:
    return path.relative_to(repository_root).as_posix()


def render_report(
    repository_root: Path,
    compose_version: str,
    results: Sequence[ValidationResult],
    fatal_error: str | None = None,
) -> str:
    """Render a deterministic Markdown validation report."""

    lines = [
        "## Docker Compose validation",
        "",
        f"- **Docker Compose version:** `{markdown_escape(compose_version)}`",
        f"- **Compose files discovered:** {len(results)}",
    ]

    if fatal_error:
        lines.extend(
            (
                "- **Result:** ❌ Validation could not run",
                "",
                "### Error",
                "",
                "```text",
                fatal_error.replace("```", r"\`\`\`"),
                "```",
                "",
            )
        )
        return "\n".join(lines)

    valid_count = sum(result.is_valid and not result.has_warnings for result in results)
    warning_count = sum(result.has_warnings for result in results)
    invalid_count = sum(not result.is_valid for result in results)

    lines.extend(
        (
            f"- **Valid:** {valid_count}",
            f"- **Valid with warnings:** {warning_count}",
            f"- **Invalid:** {invalid_count}",
            "",
            "| Compose file | Interpolation environment files | Env validation | Compose validation |",
            "| --- | --- | --- | --- |",
        )
    )

    for result in results:
        compose_display = display_path(repository_root, result.compose_file)
        if result.environment_files:
            env_display = ", ".join(
                f"`{markdown_escape(display_path(repository_root, path))}`"
                for path in result.environment_files
            )
        else:
            env_display = "None"

        if result.environment_validation is None:
            environment_status = "Not present"
        elif result.environment_validation.is_valid:
            environment_status = "✅ Valid"
        else:
            environment_status = "❌ Invalid"

        if result.return_code != 0:
            compose_status = "❌ Invalid"
        elif result.diagnostics:
            compose_status = "⚠️ Valid with warnings"
        else:
            compose_status = "✅ Valid"

        lines.append(
            f"| `{markdown_escape(compose_display)}` | {env_display} | "
            f"{environment_status} | {compose_status} |"
        )

    has_diagnostics = any(
        result.diagnostics
        or (
            result.environment_validation is not None
            and result.environment_validation.diagnostics
        )
        for result in results
    )
    if has_diagnostics:
        lines.extend(("", "### Diagnostics", ""))
        for result in results:
            environment_validation = result.environment_validation
            if (
                environment_validation is not None
                and environment_validation.diagnostics
            ):
                environment_display = display_path(
                    repository_root, environment_validation.environment_file
                )
                lines.extend(
                    (
                        f"#### Environment error: `{environment_display}`",
                        "",
                        "```text",
                        "\n".join(environment_validation.diagnostics).replace(
                            "```", r"\`\`\`"
                        ),
                        "```",
                        "",
                    )
                )

            if result.diagnostics:
                compose_display = display_path(repository_root, result.compose_file)
                heading = "Error" if result.return_code != 0 else "Warning"
                lines.extend(
                    (
                        f"#### Compose {heading.lower()}: `{compose_display}`",
                        "",
                        "```text",
                        result.diagnostics.replace("```", r"\`\`\`"),
                        "```",
                        "",
                    )
                )

    return "\n".join(lines).rstrip() + "\n"


def workflow_command_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit_annotations(
    repository_root: Path,
    results: Sequence[ValidationResult],
) -> None:
    """Create GitHub warning/error annotations for diagnostics."""

    for result in results:
        environment_validation = result.environment_validation
        if environment_validation is not None and environment_validation.diagnostics:
            path = display_path(
                repository_root, environment_validation.environment_file
            )
            message = workflow_command_escape(
                "\n".join(environment_validation.diagnostics)
            )
            print(f"::error file={path}::{message}")

        if result.diagnostics:
            level = "warning" if result.return_code == 0 else "error"
            path = display_path(repository_root, result.compose_file)
            message = workflow_command_escape(result.diagnostics)
            print(f"::{level} file={path}::{message}")


def write_report(report_path: Path, content: str) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(content, encoding="utf-8", newline="\n")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8", newline="\n") as summary:
            summary.write(content)


def main() -> int:
    arguments = parse_arguments()
    repository_root = arguments.repository_root.resolve()
    compose_version = get_compose_version()

    try:
        application_library = resolve_inside_repository(
            repository_root,
            arguments.application_library,
        )
        report_path = resolve_inside_repository(repository_root, arguments.report)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    fatal_error: str | None = None
    results: list[ValidationResult] = []

    if not repository_root.is_dir():
        fatal_error = f"Repository root does not exist: {repository_root}"
    elif not application_library.is_dir():
        fatal_error = (
            f"Application Library directory does not exist: {application_library}"
        )
    else:
        compose_files = discover_compose_files(application_library)
        if not compose_files:
            fatal_error = (
                "No files matching `*-compose.yaml` or `*-compose.yml` "
                "were found under the Application Library."
            )
        else:
            for compose_file in compose_files:
                service_env_file = discover_service_env_file(compose_file)
                environment_validation = (
                    validate_environment_file(service_env_file)
                    if service_env_file is not None
                    else None
                )
                environment_files = discover_interpolation_env_files(compose_file)
                result = validate_compose_file(
                    repository_root,
                    compose_file,
                    environment_files,
                    environment_validation,
                )
                results.append(result)

                relative_path = display_path(repository_root, compose_file)
                if not result.is_valid:
                    print(f"INVALID: {relative_path}")
                elif result.has_warnings:
                    print(f"VALID WITH WARNINGS: {relative_path}")
                else:
                    print(f"VALID: {relative_path}")

    report = render_report(
        repository_root,
        compose_version,
        results,
        fatal_error=fatal_error,
    )
    write_report(report_path, report)

    if fatal_error:
        print(f"::error::{workflow_command_escape(fatal_error)}")
        return 1

    emit_annotations(repository_root, results)

    invalid_count = sum(not result.is_valid for result in results)
    warning_count = sum(result.has_warnings for result in results)

    print(
        f"Validated {len(results)} Compose file(s): "
        f"{invalid_count} invalid, {warning_count} with warnings."
    )
    return 1 if invalid_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
