#!/usr/bin/env python3
"""Validate safe project configuration without reading local .env values."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
REQUIRED_ENV_KEYS = {
    "BOT_TOKEN",
    "DATABASE_URL",
    "ADMIN_PASSWORD",
    "LLM_API_KEY",
    "LLM_MODEL",
}


def read_env_example(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        values[key.strip()] = value.strip()
    return values


def git_command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT_DIR,
        check=False,
        capture_output=True,
        text=True,
    )


def main() -> int:
    errors: list[str] = []
    env_example = ROOT_DIR / ".env.example"

    if not env_example.exists():
        errors.append(".env.example is missing.")
    else:
        values = read_env_example(env_example)
        missing = sorted(REQUIRED_ENV_KEYS - values.keys())
        if missing:
            errors.append(f".env.example is missing keys: {', '.join(missing)}.")

        empty = sorted(key for key in REQUIRED_ENV_KEYS if not values.get(key))
        if empty:
            errors.append(f".env.example has empty values: {', '.join(empty)}.")

    ignored = git_command("check-ignore", "--no-index", "-q", ".env")
    if ignored.returncode != 0:
        errors.append(".env is not ignored by .gitignore.")

    tracked = git_command("ls-files", "--error-unmatch", ".env")
    if tracked.returncode == 0:
        errors.append(".env is tracked by Git and must be removed from version control.")

    dockerfile = ROOT_DIR / "Dockerfile.render"
    if not dockerfile.exists():
        errors.append("Dockerfile.render is missing.")
    elif "app.render_app:app" not in dockerfile.read_text(encoding="utf-8"):
        errors.append("Dockerfile.render does not start app.render_app:app.")

    if not (ROOT_DIR / "frontend" / "package-lock.json").exists():
        errors.append("frontend/package-lock.json is missing; npm ci would not be reproducible.")

    if not (ROOT_DIR / ".github" / "workflows" / "preflight.yml").exists():
        errors.append(".github/workflows/preflight.yml is missing.")

    if errors:
        print("ERROR: configuration check failed.")
        for error in errors:
            print(f" - {error}")
        return 1

    print("OK: configuration is safe and complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
