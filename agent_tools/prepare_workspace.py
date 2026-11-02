#!/usr/bin/env python3
"""Copy research inputs into an isolated agent workspace with a hash manifest."""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_EXTENSIONS = {".xlsx", ".xlsm", ".csv", ".tsv", ".docx"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_source_name(path: Path, used: set[str]) -> str:
    base = "_".join(path.name.split()) or "source"
    name = base
    suffix = 2
    while name in used:
        name = f"{base}_{suffix}"
        suffix += 1
    used.add(name)
    return name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="New or existing agent working directory.")
    parser.add_argument("sources", nargs="+", type=Path, help="Files or folders to copy.")
    parser.add_argument(
        "--extensions",
        default=",".join(sorted(DEFAULT_EXTENSIONS)),
        help="Comma-separated allowed extensions.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    input_dir = workspace / "input"
    manifest_dir = workspace / "manifests"
    input_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    allowed = {
        extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        for extension in args.extensions.split(",")
        if extension.strip()
    }

    records: list[dict[str, str | int]] = []
    used_names: set[str] = set()
    for source_arg in args.sources:
        source = source_arg.expanduser().resolve()
        if not source.exists():
            raise SystemExit(f"Source not found: {source}")
        source_root = source if source.is_dir() else source.parent
        source_name = safe_source_name(source_root, used_names)
        candidates = source.rglob("*") if source.is_dir() else [source]
        for path in sorted(candidate for candidate in candidates if candidate.is_file()):
            if path.name.startswith("~$") or path.suffix.lower() not in allowed:
                continue
            relative = path.relative_to(source_root)
            destination = input_dir / source_name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                source_hash = sha256(path)
                if sha256(destination) != source_hash:
                    raise SystemExit(f"Refusing to overwrite a different workspace file: {destination}")
                status = "already present"
            else:
                shutil.copy2(path, destination)
                source_hash = sha256(path)
                status = "copied"
            records.append(
                {
                    "source_path": str(path),
                    "workspace_path": str(destination.relative_to(workspace)),
                    "size_bytes": path.stat().st_size,
                    "sha256": source_hash,
                    "status": status,
                }
            )

    manifest = manifest_dir / "source_manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source_path", "workspace_path", "size_bytes", "sha256", "status"],
        )
        writer.writeheader()
        writer.writerows(records)
    (manifest_dir / "prepared_at_utc.txt").write_text(
        datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n", encoding="utf-8"
    )
    print(f"Prepared {len(records)} file(s) in {input_dir}")
    print(f"Wrote {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
