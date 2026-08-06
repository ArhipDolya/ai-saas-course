#!/usr/bin/env python3
"""Run the project's build and secret-safety checks before a handoff or deploy."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIRECTORY = PROJECT_ROOT / "frontend"
SECRET_SCAN_EXCLUDE_PATTERN = (
    r"(^|/)\.env$|(^|/)(?:\.venv|node_modules|\.git|__pycache__|frontend/dist)/"
)


class PreflightError(Exception):
    """A check failed without exposing command output that may contain secrets."""


def print_step(number: int, title: str) -> None:
    print(f"\n[{number}/5] {title}", flush=True)


def run_command(command: list[str], *, cwd: Path = PROJECT_ROOT) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as error:
        raise PreflightError(
            f"Не знайдено команду {command[0]!r}. Встанови потрібний інструмент і повтори."
        ) from error
    except subprocess.CalledProcessError as error:
        raise PreflightError(
            f"Команда завершилась з кодом {error.returncode}: {' '.join(command)}"
        ) from error


def find_detect_secrets() -> str:
    local_command = PROJECT_ROOT / ".venv" / "bin" / "detect-secrets"
    if local_command.is_file():
        return str(local_command)

    system_command = shutil.which("detect-secrets")
    if system_command:
        return system_command

    raise PreflightError(
        "detect-secrets не встановлено. Встанови його у .venv або додай до PATH."
    )


def check_no_secrets() -> None:
    command = [
        find_detect_secrets(),
        "scan",
        "--all-files",
        "--exclude-files",
        SECRET_SCAN_EXCLUDE_PATTERN,
        ".",
    ]
    print(f"$ {' '.join(command)}", flush=True)

    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        raise PreflightError(
            "detect-secrets не зміг завершити сканування. "
            "Перевір його налаштування та повтори команду вручну."
        ) from error

    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise PreflightError("detect-secrets повернув неочікуваний формат звіту.") from error

    results = report.get("results", {})
    if not isinstance(results, dict):
        raise PreflightError("detect-secrets повернув невалідний звіт.")

    findings = {
        filename: matches
        for filename, matches in results.items()
        if isinstance(matches, list) and matches
    }
    if findings:
        filenames = ", ".join(sorted(findings))
        finding_count = sum(len(matches) for matches in findings.values())
        raise PreflightError(
            f"detect-secrets знайшов {finding_count} потенційних секретів: {filenames}. "
            "Не додавай allowlist без ручної перевірки кожного збігу."
        )

    print("Потенційних секретів не знайдено.", flush=True)


def main() -> None:
    print(f"Preflight для: {PROJECT_ROOT}", flush=True)

    print_step(1, "Компіляція Python-коду")
    run_command([sys.executable, "-m", "compileall", "-q", "app", "scripts"])

    print_step(2, "Збірка React frontend")
    run_command(["npm", "run", "build"], cwd=FRONTEND_DIRECTORY)

    print_step(3, "Збірка локального Docker-образу")
    run_command(
        ["docker", "build", "--file", "Dockerfile", "--tag", "finance-saas-preflight:local", "."]
    )

    print_step(4, "Збірка Docker-образу для Render")
    run_command(
        [
            "docker",
            "build",
            "--file",
            "Dockerfile.render",
            "--tag",
            "finance-saas-preflight:render",
            ".",
        ]
    )

    print_step(5, "Пошук секретів через detect-secrets")
    check_no_secrets()

    print("\nPreflight успішно завершено.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except PreflightError as error:
        print(f"\nPreflight не пройдено: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
