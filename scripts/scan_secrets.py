#!/usr/bin/env python3
"""Run detect-secrets against source files without reading local .env values."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def is_local_env(path: Path) -> bool:
    return path.name == ".env" or (
        path.name.startswith(".env.") and path.name != ".env.example"
    )


def find_detect_secrets() -> str | None:
    sibling_binary = Path(sys.executable).with_name("detect-secrets")
    if sibling_binary.exists():
        return str(sibling_binary)

    return shutil.which("detect-secrets")


def project_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT_DIR,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Unable to list project files with git.")

    files: list[str] = []
    for raw_path in result.stdout.splitlines():
        relative_path = Path(raw_path)
        absolute_path = ROOT_DIR / relative_path
        if is_local_env(relative_path) or not absolute_path.is_file():
            continue
        files.append(raw_path)

    return files


def main() -> int:
    detect_secrets = find_detect_secrets()
    if detect_secrets is None:
        print(
            "ERROR: detect-secrets is not installed. "
            "Run: ./.venv/bin/python -m pip install -r requirements-dev.txt"
        )
        return 2

    files = project_files()
    if not files:
        print("ERROR: no project files were found for secret scanning.")
        return 2

    result = subprocess.run(
        [detect_secrets, "scan", "--no-verify", *files],
        cwd=ROOT_DIR,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("ERROR: detect-secrets could not complete the scan.")
        return result.returncode

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("ERROR: detect-secrets returned an invalid scan result.")
        return 2

    findings = payload.get("results", {})
    if not findings:
        print("OK: no potential hardcoded secrets found.")
        return 0

    print("ERROR: potential hardcoded secrets found; values are redacted.")
    for filename, entries in sorted(findings.items()):
        for entry in entries:
            line = entry.get("line_number", "?")
            secret_type = entry.get("type", "Unknown secret")
            print(f" - {filename}:{line} ({secret_type})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
