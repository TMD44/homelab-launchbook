# GitHub Actions Tools

Validation and automation workflows live in `.github/workflows/`. Their supporting configuration and helper files live in `.github/actions/`.

All validators check their **full configured scope on every run**, not only changed files.

This README provides a high-level overview. The workflow files and their tool-specific configuration are the **source of truth** for triggers, scope, exclusions, permissions, and implementation details.

```text
╭────────────────────────── VALIDATORS ──────────────────────────╮

    Markdown  ─→ CI + Summary + Annotations
    Python    ─→ CI + Summary + Annotations
    YAML      ─→ CI + Summary + Annotations
    Shell     ─→ CI + Summary + Annotations
    Spelling  ─→ CI + Summary + Annotations

    Compose   ─→ CI + Summary + Annotations + Artifact
    Secrets   ─→ CI + Summary + Annotations + Artifact
    Workflows ─→ CI + Summary + Annotations + Artifact
    Links     ─→ CI + Summary               + Artifact
                  Monthly: 1st day 05:17 Europe/Budapest

╰───────────────────────────────────────────────────────────────╯

╭───────────────────────── AUTOMATION ──────────────────────────╮

    Versions  ─→ Scheduled maintenance → PR when needed
                  Weekly: Sunday 04:17 Europe/Budapest

╰───────────────────────────────────────────────────────────────╯
```

## Validator Tools

| Validator | What it checks | Scope | Runs | Output |
| ------------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| **Markdown** | Markdown formatting and style | All `*.md` files | PR → `master`, push → `master`, manual | CI + Summary + annotations |
| **Python** | Ruff linting and formatting | All repository Python files, respecting Git ignore rules | PR → `master`, push → `master`, manual | CI + Summary + annotations |
| **YAML** | General YAML syntax and style | Repository YAML except Compose files, GitHub workflows, and explicitly excluded templates | PR → `master`, push → `master`, manual | CI + Summary + annotations |
| **Shell** | ShellCheck linting | All tracked `.sh`, `.bash`, `.bats`, `.dash`, and `.ksh` files | PR → `master`, push → `master`, manual | CI + Summary + annotations |
| **Spelling** | Documentation spelling | All Markdown files | PR → `master`, push → `master`, manual | CI + Summary + annotations |
| **Compose** | Docker Compose and matching environment configuration | All `*-compose.yaml` / `*-compose.yml` files under `Application Library` | PR → `master`, push → `master`, manual | CI + Summary + annotations + report artifact |
| **Secrets** | Leaked secrets | Full Git history | PR → `master`, push → `master`, manual | CI + redacted Summary + annotations + report artifacts |
| **Workflows** | GitHub Actions syntax, embedded Shell/Python, and security | All GitHub Actions workflows | PR → `master`, push → `master`, manual | CI + Summary + annotations + report artifact |
| **Links** | Internal/external Markdown links and heading anchors | All Markdown files | PR → `master`, push → `master`, manual, **1st day of each month, 05:17 Europe/Budapest** | CI + Summary + report artifact |

## Automation Tools

| Automation | What it checks | Scope | Runs | Output |
| ------------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| **Versions** | Application Library service versions | Configured services and the generated `## Versions` section | **Sunday 04:17 Europe/Budapest** + manual | Creates or updates a PR when changes are needed and enables auto-merge |

### Required setup for `Versions` automation

The `automation-application-library-versions.yml` workflow requires the following one-time GitHub configuration:

1. Create a **fine-grained personal access token** under **Profile → Settings → Developer settings → Personal access tokens → Fine-grained tokens**. Limit it to the `homelab-launchbook` repository and grant only **Contents: Read and write** and **Pull requests: Read and write**.
2. Add the token under **Repository → Settings → Secrets and variables → Actions → Secrets → New repository secret** with the exact name `AUTOMATION_APPLICATION_LIBRARY_VERSIONS_TOKEN`.
3. Enable **Allow GitHub Actions to create and approve pull requests** under **Settings → Actions → General → Workflow permissions**, and **Allow auto-merge** under **Settings → General → Pull Requests**.

## Reporting

- **Summary** — quick result and useful failure details in the GitHub Actions Summary tab.
- **Annotations** — file/line findings directly in GitHub where supported.
- **Artifacts** — retained only when a detailed downloadable report is useful.
- **CI failure** — validation problems cause the workflow to fail.

The **Versions** workflow is maintenance automation rather than a validator. It is restricted to updating the generated `## Versions` section of `Application Library/README.md`.
