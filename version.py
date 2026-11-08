"""Application version shown in the UI, the REST API and /metrics.

Docker builds pass APP_VERSION / APP_REVISION as build args (see Dockerfile and the CI workflow).
Running from a git checkout falls back to `git describe`; anything else reports "dev".
"""
import os
import subprocess
from functools import lru_cache
from typing import Dict

_UNKNOWN_REVISION = "unknown"


def _git(*args: str) -> str:
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo_dir, capture_output=True, text=True, timeout=5, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


@lru_cache(maxsize=1)
def get_version_info() -> Dict[str, str]:
    """Return {'version': ..., 'revision': ...}; resolved once per process."""
    version = os.getenv("APP_VERSION", "").strip()
    revision = os.getenv("APP_REVISION", "").strip()
    if not version:
        version = _git("describe", "--tags", "--always", "--dirty") or "dev"
    if not revision:
        revision = _git("rev-parse", "HEAD") or _UNKNOWN_REVISION
    return {"version": version, "revision": revision}


def get_version_display() -> str:
    """Short human-readable form, e.g. 'v0.8' or 'main (1efa593)'."""
    info = get_version_info()
    version, revision = info["version"], info["revision"]
    if revision == _UNKNOWN_REVISION:
        return version
    short = revision[:7]
    # git describe output (v0.7-45-g1efa593) already embeds the commit.
    if short in version:
        return version
    return f"{version} ({short})"
