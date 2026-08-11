#!/usr/bin/env python3
"""Automate the Application Library Versions section in the README.

Current versions are read from configured Docker Compose and environment files.
Stable GitHub Releases are checked against exact Docker Hub tags. The newest
GitHub release with a matching image tag is treated as the latest verified version.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

from packaging.version import InvalidVersion, Version
import yaml

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIRECTORY.parents[2]
CONFIG_PATH = SCRIPT_DIRECTORY / "services.json"
APPLICATION_LIBRARY_PATH = REPOSITORY_ROOT / "Application Library"
OUTPUT_PATH = APPLICATION_LIBRARY_PATH / "README.md"
VERSIONS_HEADING = "## Versions"

_GITHUB_REPOSITORY_PATTERN = re.compile(r"^[^/\s]+/[^/\s]+$")
_DOCKER_HUB_REPOSITORY_PATTERN = re.compile(r"^[^/\s]+/[^/\s]+$")
_INTERPOLATION_PATTERN = re.compile(
    r"\$\{(?P<braced_var>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:(?P<operator>:-|-|:\?|\?)(?P<operand>[^}]*))?\}"
    r"|\$(?P<simple_var>[A-Za-z_][A-Za-z0-9_]*)"
)
_PLACEHOLDER_MARKERS = ("replace_me", "owner/repository", "todo", "example")


@dataclass(frozen=True)
class JsonResponse:
    data: Any
    headers: Mapping[str, str]


class HttpNotFoundError(RuntimeError):
    """Raised when an exact remote resource does not exist."""


@dataclass(frozen=True)
class GitHubRelease:
    tag_name: str
    html_url: str
    published_at: str


@dataclass(frozen=True)
class GitHubReleaseBatch:
    releases: dict[Version, GitHubRelease]
    has_more: bool


@dataclass(frozen=True)
class DockerHubTag:
    name: str
    last_updated: str
    html_url: str


@dataclass(frozen=True)
class InterpolationUse:
    variable_name: str
    output_start: int
    output_end: int


@dataclass(frozen=True)
class CurrentVersionInfo:
    raw_tag: str
    parsed_version: Version | None
    source_display: str
    source_rel_path: str
    is_unpinned: bool = False
    is_unverifiable: bool = False


@dataclass(frozen=True)
class ServiceResult:
    category: str
    name: str
    current_info: CurrentVersionInfo
    latest_github_release: tuple[Version, GitHubRelease] | None = None
    verified_docker_tag: tuple[Version, DockerHubTag] | None = None
    latest_verified_version: tuple[Version, GitHubRelease] | None = None
    update_status: str = ""
    source_comparison: str = ""


@dataclass(frozen=True)
class ManagedMarkdownSection:
    """Character ranges and immutable content around the managed section body."""

    heading_start: int
    body_start: int
    body_end: int
    prefix: str
    body: str
    suffix: str
    newline: str
    section_reaches_eof: bool
    file_ends_with_newline: bool


def resolve_repository_path(relative_path: str, description: str) -> Path:
    """Resolve and validate a repository-relative path."""
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise RuntimeError(
            f"{description} must be repository-relative: {relative_path}"
        )

    root = REPOSITORY_ROOT.resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise RuntimeError(f"{description} escapes repository root: {relative_path}")
    return resolved


def contains_placeholder(value: object) -> bool:
    """Return whether a required string is empty or contains a placeholder."""
    if not isinstance(value, str) or not value.strip():
        return True
    lowered = value.strip().lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def merged_version_patterns(
    service: Mapping[str, Any], defaults: Mapping[str, Any]
) -> dict[str, str]:
    """Merge default and per-service version regex patterns."""
    patterns = dict(defaults.get("version_patterns", {}))
    overrides = service.get("version_patterns", {})
    if overrides is not None:
        if not isinstance(overrides, dict):
            raise RuntimeError("version_patterns must be an object")
        patterns.update(overrides)
    return patterns


def combined_ignored_tags(
    service: Mapping[str, Any], defaults: Mapping[str, Any]
) -> list[str]:
    """Combine ignored tags while preserving deterministic order."""
    default_tags = defaults.get("ignored_tags", [])
    service_tags = service.get("ignored_tags", [])
    if not isinstance(default_tags, list) or not isinstance(service_tags, list):
        raise RuntimeError("ignored_tags must be arrays")

    unique: dict[str, str] = {}
    for tag in [*default_tags, *service_tags]:
        if not isinstance(tag, str) or not tag.strip():
            raise RuntimeError("ignored_tags entries must be non-empty strings")
        unique.setdefault(tag.lower(), tag)
    return list(unique.values())


def parse_dotenv_file(filepath: Path) -> dict[str, str]:
    """Parse a dotenv file without executing its contents."""
    env_vars: dict[str, str] = {}

    with filepath.open("r", encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise RuntimeError(
                    f"Invalid environment variable name in {filepath} "
                    f"at line {line_number}."
                )

            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]

            env_vars[key] = value

    return env_vars


def interpolate_compose_value(
    value: str, env_vars: Mapping[str, str]
) -> tuple[str, list[InterpolationUse]]:
    """Interpolate supported Compose variable forms and track value sources."""
    output_parts: list[str] = []
    uses: list[InterpolationUse] = []
    cursor = 0
    output_length = 0

    for match in _INTERPOLATION_PATTERN.finditer(value):
        prefix = value[cursor : match.start()]
        output_parts.append(prefix)
        output_length += len(prefix)

        variable_name = match.group("braced_var") or match.group("simple_var")
        operator = match.group("operator")
        operand = match.group("operand") or ""
        is_set = variable_name in env_vars
        variable_value = env_vars.get(variable_name, "")
        is_nonempty = is_set and variable_value != ""
        used_environment_value = False

        if match.group("simple_var") is not None or operator is None:
            if not is_set:
                raise RuntimeError(
                    f"Required environment variable '{variable_name}' is unset."
                )
            replacement = variable_value
            used_environment_value = True
        elif operator == ":-":
            if is_nonempty:
                replacement = variable_value
                used_environment_value = True
            else:
                replacement = operand
        elif operator == "-":
            if is_set:
                replacement = variable_value
                used_environment_value = True
            else:
                replacement = operand
        elif operator == ":?":
            if not is_nonempty:
                detail = operand or "variable is unset or empty"
                raise RuntimeError(
                    f"Required environment variable '{variable_name}' is unset or empty: {detail}"
                )
            replacement = variable_value
            used_environment_value = True
        elif operator == "?":
            if not is_set:
                detail = operand or "variable is unset"
                raise RuntimeError(
                    f"Required environment variable '{variable_name}' is unset: {detail}"
                )
            replacement = variable_value
            used_environment_value = True
        else:  # Defensive; the regex limits the supported operators.
            raise RuntimeError(
                f"Unsupported Compose interpolation operator: {operator}"
            )

        replacement_start = output_length
        output_parts.append(replacement)
        output_length += len(replacement)
        if used_environment_value:
            uses.append(
                InterpolationUse(
                    variable_name=variable_name,
                    output_start=replacement_start,
                    output_end=output_length,
                )
            )

        cursor = match.end()

    suffix = value[cursor:]
    output_parts.append(suffix)
    return "".join(output_parts), uses


def split_image_reference(
    image: str,
) -> tuple[str, str | None, str | None]:
    """Split an image reference into normalized repository, tag, and digest."""
    reference = image.strip()
    if not reference:
        raise RuntimeError("Docker image reference is empty.")

    digest: str | None = None
    if "@" in reference:
        reference, digest = reference.split("@", 1)
        if not digest:
            raise RuntimeError(f"Docker image reference has an empty digest: {image}")

    last_slash = reference.rfind("/")
    last_colon = reference.rfind(":")
    if last_colon > last_slash:
        repository = reference[:last_colon]
        tag = reference[last_colon + 1 :]
        if not tag:
            raise RuntimeError(f"Docker image reference has an empty tag: {image}")
    else:
        repository = reference
        tag = None

    for prefix in ("docker.io/", "index.docker.io/"):
        if repository.startswith(prefix):
            repository = repository[len(prefix) :]
            break

    if "/" not in repository:
        repository = f"library/{repository}"

    return repository, tag, digest


def image_tag_span(image: str) -> tuple[int, int] | None:
    """Return the character span occupied by an image tag."""
    digest_index = image.find("@")
    reference_end = digest_index if digest_index >= 0 else len(image)
    last_slash = image.rfind("/", 0, reference_end)
    last_colon = image.rfind(":", 0, reference_end)
    if last_colon <= last_slash:
        return None
    return last_colon + 1, reference_end


def extract_version(
    raw_value: str, pattern: str, ignored_tags: list[str]
) -> Version | None:
    """Extract a stable PEP 440-compatible version using a configured regex."""
    cleaned = raw_value.strip()
    ignored = {tag.lower() for tag in ignored_tags}
    if cleaned.lower() in ignored:
        return None

    match = re.fullmatch(pattern, cleaned)
    if match is None:
        return None

    extracted = match.group("version")
    try:
        version = Version(extracted)
    except InvalidVersion:
        return None

    if version.is_prerelease or version.is_devrelease:
        return None
    return version


def request_json(
    url: str,
    headers: Mapping[str, str],
    attempts: int = 3,
    timeout: int = 30,
) -> JsonResponse:
    """Make an HTTP GET request and parse a JSON response with bounded retries."""
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers=dict(headers))
            with urllib.request.urlopen(request, timeout=timeout) as response:
                try:
                    data = json.load(response)
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise RuntimeError(
                        f"Invalid JSON response while requesting {url}"
                    ) from error
                return JsonResponse(data=data, headers=dict(response.headers.items()))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise HttpNotFoundError(f"HTTP 404 while requesting {url}") from error

            retryable = error.code == 429 or 500 <= error.code <= 599
            if not retryable or attempt == attempts:
                raise RuntimeError(
                    f"HTTP {error.code} while requesting {url}"
                ) from error
        except urllib.error.URLError as error:
            if attempt == attempts:
                raise RuntimeError(
                    f"Network error while requesting {url}: {error.reason}"
                ) from error

        time.sleep(2 ** (attempt - 1))

    raise RuntimeError(f"Failed to request {url}")


def github_link_has_next(link_header: str) -> bool:
    """Return whether a GitHub Link header advertises a next page."""
    return any(
        'rel="next"' in part or "rel=next" in part for part in link_header.split(",")
    )


def fetch_github_releases(
    github_repository: str,
    headers: Mapping[str, str],
    pattern: str,
    ignored_tags: list[str],
    release_limit: int,
) -> GitHubReleaseBatch:
    """Fetch a bounded set of recent stable GitHub Releases.

    The tracker only needs enough recent release candidates to locate the newest
    version that also has an exact Docker Hub tag. It must not require scanning a
    repository's complete release history, because long-lived projects such as
    Nextcloud can contain many hundreds of historical releases.
    """
    owner, repository = github_repository.split("/", 1)
    owner = urllib.parse.quote(owner, safe="")
    repository = urllib.parse.quote(repository, safe="")
    releases: dict[Version, GitHubRelease] = {}
    page = 1
    has_more = False

    while len(releases) < release_limit:
        if page > 1:
            time.sleep(0.3)

        url = (
            f"https://api.github.com/repos/{owner}/{repository}/releases"
            f"?per_page=100&page={page}"
        )
        response = request_json(url, headers)
        data = response.data
        if not isinstance(data, list):
            raise RuntimeError(
                f"Unexpected GitHub API response for '{github_repository}'."
            )

        if not data:
            break

        has_next_page = github_link_has_next(response.headers.get("Link", ""))
        for item_index, item in enumerate(data):
            if not isinstance(item, dict):
                raise RuntimeError(
                    f"Malformed GitHub release entry for '{github_repository}'."
                )
            if item.get("draft") or item.get("prerelease"):
                continue

            tag_name = item.get("tag_name")
            html_url = item.get("html_url")
            published_at = item.get("published_at") or item.get("created_at") or ""
            if not isinstance(tag_name, str) or not isinstance(html_url, str):
                raise RuntimeError(
                    f"Malformed GitHub release data for '{github_repository}'."
                )

            version = extract_version(tag_name, pattern, ignored_tags)
            if version is not None and version not in releases:
                releases[version] = GitHubRelease(
                    tag_name=tag_name,
                    html_url=html_url,
                    published_at=str(published_at),
                )
                if len(releases) >= release_limit:
                    has_more = item_index < len(data) - 1 or has_next_page
                    break

        if len(releases) >= release_limit:
            break
        if not has_next_page:
            break
        page += 1

    if not releases:
        raise RuntimeError(
            f"No valid stable GitHub Releases were found for '{github_repository}'."
        )
    return GitHubReleaseBatch(releases=releases, has_more=has_more)


def docker_tag_templates(
    service: Mapping[str, Any], defaults: Mapping[str, Any]
) -> list[str]:
    """Return validated Docker tag templates for exact-tag lookups."""
    raw_templates = service.get(
        "docker_tag_templates",
        defaults.get(
            "docker_tag_templates",
            ["{github_tag}", "{version}", "v{version}"],
        ),
    )
    if not isinstance(raw_templates, list) or not raw_templates:
        raise RuntimeError("docker_tag_templates must be a non-empty array")

    templates: list[str] = []
    for template in raw_templates:
        if not isinstance(template, str) or not template.strip():
            raise RuntimeError("docker_tag_templates entries must be non-empty strings")
        if "{github_tag}" not in template and "{version}" not in template:
            raise RuntimeError(
                "Each docker_tag_templates entry must contain {github_tag} "
                "or {version}."
            )

        probe = template.replace("{github_tag}", "release-tag").replace(
            "{version}", "1.2.3"
        )
        if "{" in probe or "}" in probe:
            raise RuntimeError(
                f"Unsupported placeholder in Docker tag template: {template}"
            )
        templates.append(template)

    return templates


def build_docker_tag_candidates(
    github_tag: str,
    version: Version,
    templates: list[str],
) -> list[str]:
    """Build deterministic candidate tag names for an exact Docker Hub lookup."""
    values = {
        "github_tag": github_tag,
        "version": str(version),
    }
    candidates: dict[str, None] = {}
    for template in templates:
        candidate = template.format(**values).strip()
        if candidate:
            candidates.setdefault(candidate, None)
    return list(candidates)


def fetch_exact_docker_hub_tag(
    docker_repository: str,
    tag_name: str,
    headers: Mapping[str, str],
) -> DockerHubTag | None:
    """Return one exact Docker Hub tag, or None when that tag does not exist."""
    namespace, repository = docker_repository.split("/", 1)
    encoded_namespace = urllib.parse.quote(namespace, safe="")
    encoded_repository = urllib.parse.quote(repository, safe="")
    encoded_tag = urllib.parse.quote(tag_name, safe="")
    url = (
        "https://hub.docker.com/v2/"
        f"namespaces/{encoded_namespace}/repositories/{encoded_repository}/"
        f"tags/{encoded_tag}"
    )

    try:
        response = request_json(url, headers)
    except HttpNotFoundError:
        return None

    data = response.data
    if not isinstance(data, dict):
        raise RuntimeError(
            f"Unexpected Docker Hub API response for '{docker_repository}:{tag_name}'."
        )

    returned_name = data.get("name")
    last_updated = data.get("last_updated") or ""
    if not isinstance(returned_name, str) or not returned_name:
        raise RuntimeError(
            f"Malformed Docker Hub tag response for '{docker_repository}:{tag_name}'."
        )

    return DockerHubTag(
        name=returned_name,
        last_updated=str(last_updated),
        html_url=format_docker_hub_url(docker_repository, returned_name),
    )


def find_latest_verified_docker_tag(
    github_releases: Mapping[Version, GitHubRelease],
    docker_repository: str,
    headers: Mapping[str, str],
    docker_pattern: str,
    ignored_tags: list[str],
    templates: list[str],
    release_check_limit: int,
    github_has_more_releases: bool,
) -> tuple[Version, GitHubRelease, DockerHubTag] | None:
    """Find the newest GitHub release with an exact matching Docker Hub tag."""
    sorted_versions = sorted(github_releases, reverse=True)
    for version in sorted_versions[:release_check_limit]:
        release = github_releases[version]
        for candidate in build_docker_tag_candidates(
            release.tag_name,
            version,
            templates,
        ):
            docker_tag = fetch_exact_docker_hub_tag(
                docker_repository,
                candidate,
                headers,
            )
            if docker_tag is None:
                continue

            parsed_docker_version = extract_version(
                docker_tag.name,
                docker_pattern,
                ignored_tags,
            )
            if parsed_docker_version == version:
                return version, release, docker_tag

    if github_has_more_releases:
        raise RuntimeError(
            f"No matching Docker Hub tag was found for the newest "
            f"{release_check_limit} valid stable GitHub releases associated with "
            f"'{docker_repository}'. Increase docker_release_check_limit or "
            "configure service-specific docker_tag_templates."
        )

    return None


def validate_positive_page_limit(value: object, field: str, service_name: str) -> None:
    """Validate an API page-limit configuration value."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeError(
            f"Service '{service_name}' has invalid {field}; expected a positive integer."
        )


def load_and_validate_config(config_path: Path) -> dict[str, Any]:
    """Load services.json and validate its schema and local references."""
    if not config_path.is_file():
        raise RuntimeError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise RuntimeError(f"Configuration root must be an object: {config_path}")
    if config.get("schema_version") != 1:
        raise RuntimeError(f"Unsupported schema_version in {config_path}")

    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict):
        raise RuntimeError("'defaults' must be an object.")
    docker_tag_templates({}, defaults)
    validate_positive_page_limit(
        defaults.get("docker_release_check_limit", 25),
        "docker_release_check_limit",
        "defaults",
    )

    services = config.get("services")
    if not isinstance(services, list) or not services:
        raise RuntimeError(f"'services' must be a non-empty array in {config_path}")

    seen_names: set[str] = set()
    seen_compose_paths: set[Path] = set()

    for index, service in enumerate(services):
        if not isinstance(service, dict):
            raise RuntimeError(f"Service index {index} must be an object.")

        service_name = service.get("name")
        if contains_placeholder(service_name):
            raise RuntimeError(f"Service index {index} has an invalid or missing name.")
        assert isinstance(service_name, str)
        name_key = service_name.casefold()
        if name_key in seen_names:
            raise RuntimeError(f"Duplicate service name '{service_name}'.")
        seen_names.add(name_key)

        category = service.get("category")
        if contains_placeholder(category):
            raise RuntimeError(f"Service '{service_name}' has an invalid category.")
        if not isinstance(service.get("enabled", True), bool):
            raise RuntimeError(f"Service '{service_name}' enabled must be a boolean.")

        compose_relative = service.get("compose_file")
        if contains_placeholder(compose_relative):
            raise RuntimeError(f"Service '{service_name}' has an invalid compose_file.")
        assert isinstance(compose_relative, str)
        compose_path = resolve_repository_path(
            compose_relative, f"Service '{service_name}' compose_file"
        )
        if compose_path in seen_compose_paths:
            raise RuntimeError(
                f"Service '{service_name}' reuses compose_file '{compose_relative}'."
            )
        seen_compose_paths.add(compose_path)
        if not compose_path.is_file():
            raise RuntimeError(
                f"Service '{service_name}' compose_file does not exist: "
                f"{compose_relative}"
            )

        env_files = service.get("env_files", [])
        if not isinstance(env_files, list):
            raise RuntimeError(f"Service '{service_name}' env_files must be an array.")
        for env_relative in env_files:
            if contains_placeholder(env_relative):
                raise RuntimeError(
                    f"Service '{service_name}' contains an invalid env_file."
                )
            assert isinstance(env_relative, str)
            env_path = resolve_repository_path(
                env_relative, f"Service '{service_name}' env_file"
            )
            if not env_path.is_file():
                raise RuntimeError(
                    f"Service '{service_name}' env_file does not exist: {env_relative}"
                )

        compose_service = service.get("compose_service")
        if contains_placeholder(compose_service):
            raise RuntimeError(
                f"Service '{service_name}' has an invalid compose_service."
            )

        with compose_path.open("r", encoding="utf-8") as compose_file:
            compose_data = yaml.safe_load(compose_file) or {}
        if not isinstance(compose_data, dict):
            raise RuntimeError(
                f"Service '{service_name}' Compose root must be a mapping."
            )
        compose_services = compose_data.get("services")
        if not isinstance(compose_services, dict):
            raise RuntimeError(
                f"Service '{service_name}' Compose file has no services mapping."
            )
        if compose_service not in compose_services:
            raise RuntimeError(
                f"Service '{service_name}' compose_service '{compose_service}' "
                f"was not found in {compose_relative}."
            )
        if not isinstance(compose_services[compose_service], dict):
            raise RuntimeError(
                f"Service '{service_name}' Compose service definition must be a mapping."
            )

        expected_image = service.get("image")
        if contains_placeholder(expected_image):
            raise RuntimeError(f"Service '{service_name}' has an invalid image.")
        assert isinstance(expected_image, str)
        _, expected_tag, expected_digest = split_image_reference(expected_image)
        if expected_tag is not None or expected_digest is not None:
            raise RuntimeError(
                f"Service '{service_name}' image must not contain a tag or digest."
            )

        if service.get("enabled", True):
            github_repository = service.get("github_repository")
            if (
                contains_placeholder(github_repository)
                or not isinstance(github_repository, str)
                or _GITHUB_REPOSITORY_PATTERN.fullmatch(github_repository) is None
            ):
                raise RuntimeError(
                    f"Service '{service_name}' has an invalid github_repository. "
                    "Expected 'owner/repository'."
                )

            docker_repository = service.get("docker_hub_repository")
            if (
                contains_placeholder(docker_repository)
                or not isinstance(docker_repository, str)
                or _DOCKER_HUB_REPOSITORY_PATTERN.fullmatch(docker_repository) is None
            ):
                raise RuntimeError(
                    f"Service '{service_name}' has an invalid docker_hub_repository. "
                    "Expected 'namespace/repository'."
                )

        patterns = merged_version_patterns(service, defaults)
        for pattern_name in ("current", "github", "docker"):
            pattern_value = patterns.get(pattern_name)
            if not isinstance(pattern_value, str) or not pattern_value:
                raise RuntimeError(
                    f"Service '{service_name}' is missing version pattern "
                    f"'{pattern_name}'."
                )
            try:
                compiled = re.compile(pattern_value)
            except re.error as error:
                raise RuntimeError(
                    f"Service '{service_name}' pattern '{pattern_name}' is invalid: "
                    f"{error}"
                ) from error
            if "version" not in compiled.groupindex:
                raise RuntimeError(
                    f"Service '{service_name}' pattern '{pattern_name}' must contain "
                    "a named 'version' group."
                )

        combined_ignored_tags(service, defaults)
        docker_tag_templates(service, defaults)
        validate_positive_page_limit(
            service.get(
                "docker_release_check_limit",
                defaults.get("docker_release_check_limit", 25),
            ),
            "docker_release_check_limit",
            service_name,
        )

    return config


def resolve_current_version(
    service: Mapping[str, Any], defaults: Mapping[str, Any]
) -> CurrentVersionInfo:
    """Resolve the configured image tag and the file that supplies it."""
    service_name = str(service["name"])
    compose_relative = str(service["compose_file"])
    compose_path = resolve_repository_path(
        compose_relative, f"Service '{service_name}' compose_file"
    )
    env_relatives = service.get("env_files", [])

    combined_env: dict[str, str] = {}
    env_variable_sources: dict[str, str] = {}
    for env_relative_value in env_relatives:
        env_relative = str(env_relative_value)
        env_path = resolve_repository_path(
            env_relative, f"Service '{service_name}' env_file"
        )
        for variable_name, variable_value in parse_dotenv_file(env_path).items():
            combined_env[variable_name] = variable_value
            env_variable_sources[variable_name] = env_relative

    with compose_path.open("r", encoding="utf-8") as compose_file:
        compose_data = yaml.safe_load(compose_file) or {}
    service_definition = compose_data["services"][service["compose_service"]]
    raw_image = service_definition.get("image")
    if not isinstance(raw_image, str) or not raw_image.strip():
        raise RuntimeError(
            f"Service '{service_name}' Compose service '{service['compose_service']}' "
            "has no valid image field."
        )

    interpolated_image, interpolation_uses = interpolate_compose_value(
        raw_image, combined_env
    )
    normalized_repository, tag, digest = split_image_reference(interpolated_image)
    expected_repository, _, _ = split_image_reference(str(service["image"]))
    if normalized_repository != expected_repository:
        raise RuntimeError(
            f"Service '{service_name}' resolved image '{normalized_repository}' does "
            f"not match configured image '{expected_repository}'."
        )

    source_relative = compose_relative
    tag_span = image_tag_span(interpolated_image)
    if tag_span is not None:
        tag_start, tag_end = tag_span
        contributing_uses = [
            use
            for use in interpolation_uses
            if use.output_start < tag_end and use.output_end > tag_start
        ]
        if contributing_uses:
            # The last contributing variable normally provides the most specific
            # portion of a constructed tag such as ${IMAGE}:${VERSION}.
            source_variable = contributing_uses[-1].variable_name
            source_relative = env_variable_sources.get(
                source_variable, compose_relative
            )

    source_display = Path(source_relative).name

    if tag is None:
        if digest is not None:
            return CurrentVersionInfo(
                raw_tag=f"sha256:{digest.removeprefix('sha256:')[:12]}…",
                parsed_version=None,
                source_display=source_display,
                source_rel_path=source_relative,
                is_unverifiable=True,
            )
        return CurrentVersionInfo(
            raw_tag="latest",
            parsed_version=None,
            source_display=source_display,
            source_rel_path=source_relative,
            is_unpinned=True,
        )

    patterns = merged_version_patterns(service, defaults)
    ignored_tags = combined_ignored_tags(service, defaults)
    parsed_version = extract_version(tag, patterns["current"], ignored_tags)
    return CurrentVersionInfo(
        raw_tag=tag,
        parsed_version=parsed_version,
        source_display=source_display,
        source_rel_path=source_relative,
        is_unpinned=parsed_version is None,
    )


def strip_line_ending(line: str) -> tuple[str, str]:
    """Split one text line into content and its exact line ending."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


def read_utf8_exact(path: Path, description: str) -> str:
    """Read UTF-8 text without universal-newline conversion."""
    if not path.is_file():
        raise RuntimeError(f"{description} not found: {path}")

    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError(f"{description} is not valid UTF-8: {path}") from error


def markdown_heading_level(line_content: str) -> int | None:
    """Return an ATX H1/H2 level, excluding deeper headings."""
    match = re.fullmatch(r" {0,3}(#{1,2})(?!#)(?:[ \t]+.*)?", line_content)
    return len(match.group(1)) if match else None


def locate_versions_section(markdown: str, description: str) -> ManagedMarkdownSection:
    """Locate the unique Versions section while ignoring fenced code blocks."""
    lines = markdown.splitlines(keepends=True)
    headings: list[tuple[int, int, int, str]] = []
    exact_versions_headings: list[tuple[int, int, str]] = []

    offset = 0
    fence_character: str | None = None
    fence_length = 0

    for line in lines:
        line_content, line_ending = strip_line_ending(line)

        if fence_character is not None:
            closing_pattern = (
                rf" {{0,3}}{re.escape(fence_character)}"
                rf"{{{fence_length},}}[ \t]*"
            )
            if re.fullmatch(closing_pattern, line_content):
                fence_character = None
                fence_length = 0
            offset += len(line)
            continue

        opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line_content)
        if opening is not None:
            marker = opening.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            offset += len(line)
            continue

        heading_level = markdown_heading_level(line_content)
        if heading_level is not None:
            headings.append((offset, offset + len(line), heading_level, line_content))
            if line_content == VERSIONS_HEADING:
                exact_versions_headings.append(
                    (offset, offset + len(line), line_ending)
                )

        offset += len(line)

    if len(exact_versions_headings) != 1:
        raise RuntimeError(
            f"{description} must contain exactly one exact "
            f"'{VERSIONS_HEADING}' heading outside fenced code blocks; "
            f"found {len(exact_versions_headings)}."
        )

    heading_start, body_start, heading_line_ending = exact_versions_headings[0]
    body_end = len(markdown)

    for candidate_start, _, candidate_level, _ in headings:
        if candidate_start > heading_start and candidate_level in {1, 2}:
            body_end = candidate_start
            break

    newline = heading_line_ending
    if not newline:
        newline_match = re.search(r"\r\n|\n|\r", markdown)
        newline = newline_match.group(0) if newline_match else "\n"

    return ManagedMarkdownSection(
        heading_start=heading_start,
        body_start=body_start,
        body_end=body_end,
        prefix=markdown[:body_start],
        body=markdown[body_start:body_end],
        suffix=markdown[body_end:],
        newline=newline,
        section_reaches_eof=body_end == len(markdown),
        file_ends_with_newline=markdown.endswith(("\r\n", "\n", "\r")),
    )


def render_managed_section_body(content: str, section: ManagedMarkdownSection) -> str:
    """Apply the README newline convention and section-boundary spacing."""
    normalized = content.rstrip("\r\n").replace("\r\n", "\n").replace("\r", "\n")
    rendered = normalized.replace("\n", section.newline)

    leading = section.newline
    if section.section_reaches_eof:
        trailing = section.newline if section.file_ends_with_newline else ""
    else:
        trailing = section.newline * 2

    return f"{leading}{rendered}{trailing}"


def update_versions_section(content: str) -> bool:
    """Replace only the body of the README's Versions section."""
    existing = read_utf8_exact(OUTPUT_PATH, "Application Library README")
    section = locate_versions_section(existing, "Application Library README")
    replacement_body = render_managed_section_body(content, section)
    updated = f"{section.prefix}{replacement_body}{section.suffix}"

    if updated == existing:
        return False

    OUTPUT_PATH.write_bytes(updated.encode("utf-8"))
    return True


def verify_versions_section_only(baseline_path: Path) -> None:
    """Prove that no README content outside the Versions body changed."""
    baseline = read_utf8_exact(baseline_path, "README baseline")
    current = read_utf8_exact(OUTPUT_PATH, "Application Library README")

    baseline_section = locate_versions_section(baseline, "README baseline")
    current_section = locate_versions_section(current, "Application Library README")

    if baseline_section.prefix != current_section.prefix:
        raise RuntimeError(
            f"Unexpected changes were detected before or in the "
            f"'{VERSIONS_HEADING}' heading."
        )

    if baseline_section.suffix != current_section.suffix:
        raise RuntimeError(
            f"Unexpected changes were detected after the '{VERSIONS_HEADING}' section."
        )


def make_relative_markdown_link(target_relative_path: str) -> str:
    """Create a URL-encoded link relative to Application Library/README.md."""
    target = resolve_repository_path(target_relative_path, "Markdown target")
    relative = os.path.relpath(target, OUTPUT_PATH.parent)
    posix_path = Path(relative).as_posix()
    encoded = urllib.parse.quote(posix_path, safe="/.")
    return encoded if encoded.startswith(".") else f"./{encoded}"


def escape_markdown_table(value: str) -> str:
    """Escape characters that would break a Markdown table cell."""
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def format_docker_hub_url(docker_repository: str, tag_name: str) -> str:
    """Create a Docker Hub web URL for a specific tag search."""
    encoded_tag = urllib.parse.quote(tag_name, safe="")
    if docker_repository.startswith("library/"):
        image_name = urllib.parse.quote(docker_repository.split("/", 1)[1], safe="")
        return f"https://hub.docker.com/_/{image_name}/tags?name={encoded_tag}"

    encoded_repository = "/".join(
        urllib.parse.quote(part, safe="") for part in docker_repository.split("/", 1)
    )
    return f"https://hub.docker.com/r/{encoded_repository}/tags?name={encoded_tag}"


def render_markdown(results: list[ServiceResult]) -> str:
    """Render deterministic content for the README's Versions section."""
    sorted_results = sorted(
        results, key=lambda result: (result.category.casefold(), result.name.casefold())
    )

    up_to_date = sum(
        result.update_status == "✅ Up to date" for result in sorted_results
    )
    updates_available = sum(
        result.update_status == "⬆️ Update available" for result in sorted_results
    )
    unpinned_or_unverifiable = sum(
        result.update_status == "⚠️ Unpinned or unverifiable"
        for result in sorted_results
    )
    source_mismatches = sum(
        result.source_comparison != "✅ Sources agree" for result in sorted_results
    )

    lines = [
        "> [!NOTE]",
        "> This section is generated automatically by GitHub Actions.",
        "> Current versions are read from the Docker Compose and environment files stored in this repository.",
        "> A version is considered verified when a stable GitHub Release has an exact Docker Hub tag that normalizes to the same version using the configured service-specific patterns.",
        "> Do not edit this section manually.",
        "",
        "| Category | Service | Current version | GitHub release | Verified Docker Hub tag | Update status | Current version source |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for result in sorted_results:
        current_cell = (
            f"`{escape_markdown_table(result.current_info.raw_tag)}`"
            if result.current_info.raw_tag
            else "—"
        )

        if result.latest_github_release is None:
            github_cell = "—"
        else:
            _, release = result.latest_github_release
            github_cell = (
                f"[`{escape_markdown_table(release.tag_name)}`]({release.html_url})"
            )

        if result.verified_docker_tag is None:
            docker_cell = "—"
        else:
            _, docker_tag = result.verified_docker_tag
            docker_cell = (
                f"[`{escape_markdown_table(docker_tag.name)}`]({docker_tag.html_url})"
            )

        source_url = make_relative_markdown_link(result.current_info.source_rel_path)
        source_label = escape_markdown_table(result.current_info.source_display)
        source_cell = f"[`{source_label}`]({source_url})"

        lines.append(
            f"| {escape_markdown_table(result.category)} "
            f"| {escape_markdown_table(result.name)} "
            f"| {current_cell} "
            f"| {github_cell} "
            f"| {docker_cell} "
            f"| {result.update_status} "
            f"| {source_cell} |"
        )

    lines.extend(
        [
            "",
            "### Summary",
            "",
            f"- **Tracked services:** {len(sorted_results)}",
            f"- **Up to date:** {up_to_date}",
            f"- **Updates available:** {updates_available}",
            f"- **Unpinned or unverifiable:** {unpinned_or_unverifiable}",
            f"- **Source mismatches:** {source_mismatches}",
            "",
        ]
    )
    return "\n".join(lines)


def process_service(
    service: Mapping[str, Any],
    defaults: Mapping[str, Any],
    github_headers: Mapping[str, str],
    docker_hub_headers: Mapping[str, str],
) -> ServiceResult:
    """Resolve and compare all versions for one configured service."""
    current_info = resolve_current_version(service, defaults)
    patterns = merged_version_patterns(service, defaults)
    ignored_tags = combined_ignored_tags(service, defaults)
    templates = docker_tag_templates(service, defaults)

    github_repository = str(service["github_repository"])
    docker_repository = str(service["docker_hub_repository"])
    release_check_limit = int(
        service.get(
            "docker_release_check_limit",
            defaults.get("docker_release_check_limit", 25),
        )
    )

    github_release_batch = fetch_github_releases(
        github_repository,
        github_headers,
        patterns["github"],
        ignored_tags,
        release_check_limit,
    )
    github_releases = github_release_batch.releases

    latest_github_version = max(github_releases)
    latest_github = (
        latest_github_version,
        github_releases[latest_github_version],
    )

    verified_match = find_latest_verified_docker_tag(
        github_releases,
        docker_repository,
        docker_hub_headers,
        patterns["docker"],
        ignored_tags,
        templates,
        release_check_limit,
        github_release_batch.has_more,
    )

    if verified_match is None:
        verified_docker = None
        latest_verified = None
        source_comparison = "⚠️ No common stable version"
    else:
        verified_version, verified_release, docker_tag = verified_match
        verified_docker = (verified_version, docker_tag)
        latest_verified = (verified_version, verified_release)
        if verified_version == latest_github_version:
            source_comparison = "✅ Sources agree"
        else:
            source_comparison = "⏳ GitHub release ahead"

    if (
        current_info.is_unpinned
        or current_info.is_unverifiable
        or current_info.parsed_version is None
    ):
        update_status = "⚠️ Unpinned or unverifiable"
    elif latest_verified is None:
        update_status = "⚠️ Cannot determine verified update"
    elif current_info.parsed_version == latest_verified[0]:
        update_status = "✅ Up to date"
    elif current_info.parsed_version < latest_verified[0]:
        update_status = "⬆️ Update available"
    else:
        update_status = "⚠️ Current version newer than verified"

    return ServiceResult(
        category=str(service["category"]),
        name=str(service["name"]),
        current_info=current_info,
        latest_github_release=latest_github,
        verified_docker_tag=verified_docker,
        latest_verified_version=latest_verified,
        update_status=update_status,
        source_comparison=source_comparison,
    )


def main() -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="Automate Homelab Launchbook Application Library version tracking."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--validate-only",
        action="store_true",
        help=(
            "Validate configuration, local version sources, and the README "
            "Versions section without network requests."
        ),
    )
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Perform the full check and print a complete Versions section "
            "without modifying the README."
        ),
    )
    modes.add_argument(
        "--verify-section-only",
        action="store_true",
        help=(
            "Verify that Application Library/README.md differs from a baseline "
            "only inside the Versions section body."
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Baseline README used together with --verify-section-only.",
    )
    args = parser.parse_args()

    try:
        if args.verify_section_only:
            if args.baseline is None:
                raise RuntimeError("--verify-section-only requires --baseline <path>.")
            verify_versions_section_only(args.baseline)
            print(
                "Verified: only the Versions section body in "
                "Application Library/README.md may have changed."
            )
            return 0

        if args.baseline is not None:
            raise RuntimeError(
                "--baseline can only be used with --verify-section-only."
            )

        readme = read_utf8_exact(OUTPUT_PATH, "Application Library README")
        locate_versions_section(readme, "Application Library README")

        config = load_and_validate_config(CONFIG_PATH)
        defaults = config.get("defaults", {})
        enabled_services = [
            service for service in config["services"] if service.get("enabled", True)
        ]

        if args.validate_only:
            for service in enabled_services:
                resolve_current_version(service, defaults)
            print("Configuration and Application Library README validation successful.")
            return 0

        github_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "homelab-launchbook-automation-application-library-versions",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        github_token = os.environ.get("GITHUB_TOKEN")
        if github_token:
            github_headers["Authorization"] = f"Bearer {github_token}"

        # Never include GITHUB_TOKEN or GitHub-specific headers in Docker Hub requests.
        docker_hub_headers = {
            "Accept": "application/json",
            "User-Agent": "homelab-launchbook-automation-application-library-versions",
        }

        results = [
            process_service(
                service,
                defaults,
                github_headers,
                docker_hub_headers,
            )
            for service in enabled_services
        ]
        content = render_markdown(results)

        if args.dry_run:
            print(f"{VERSIONS_HEADING}\n\n{content}", end="")
            return 0

        if update_versions_section(content):
            print("Updated the Versions section in Application Library/README.md.")
        else:
            print(
                "The Versions section in Application Library/README.md "
                "is already up to date."
            )
        return 0

    except (json.JSONDecodeError, yaml.YAMLError, RuntimeError, OSError) as error:
        print(f"Application Library version automation error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
