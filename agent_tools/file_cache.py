#!/usr/bin/env python3
"""Tiny on-disk JSON cache keyed by (file path, mtime, size), so a per-file
computation (e.g. parsing one workbook) is skipped on a later run when the
file hasn't changed. Not a general-purpose cache: values must be JSON-safe.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def load_cache(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache_path: Path, cache: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache))


def fingerprint(path: Path) -> dict[str, float | int]:
    stat = path.stat()
    return {"mtime": stat.st_mtime, "size": stat.st_size}


def get_or_compute(cache: dict[str, Any], path: Path, compute: Callable[[], Any]) -> tuple[Any, bool]:
    """Returns (value, was_cache_hit). compute() runs only on a miss (new
    file, or an existing file whose mtime/size changed since it was cached)."""
    key = str(path)
    fp = fingerprint(path)
    entry = cache.get(key)
    if entry is not None and entry.get("mtime") == fp["mtime"] and entry.get("size") == fp["size"]:
        return entry["value"], True
    value = compute()
    cache[key] = {**fp, "value": value}
    return value, False


def write_if_changed(path: Path, content: str) -> bool:
    """Write content to path only if it differs from what's already there
    (or the file doesn't exist yet). Returns True if a write happened.

    This matters for any downstream Make-style staleness check (see
    run_latency_pipeline.py): a step that unconditionally rewrites its
    output every run bumps that file's mtime even when the content is
    byte-identical, which makes every later stage that depends on it look
    stale forever. Leaving an unchanged file's mtime alone is what lets
    staleness checks actually skip work."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == content:
        return False
    path.write_text(content)
    return True
