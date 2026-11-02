#!/usr/bin/env python3
"""Upload the weekly report's plot PNGs to the Confluence page as attachments.

Credentials come from ~/.confluence_api_token (chmod 600), a two-line file:

    CONFLUENCE_EMAIL = you@example.com
    CONFLUENCE_API_TOKEN = <your token>

This only uploads/refreshes the attachments (POST .../child/attachment,
which Confluence Cloud treats as a new version when the filename already
exists) and prints each attachment's media fileId + collection, which the
page body's <figure data-type="media-single"> embed needs
(data-id=fileId, data-collection=collectionName) — see
getContentFormatGuide's Media/Attachment section for the exact HTML. This
script does not edit the page body itself; the fileId is stable across
re-uploads of the same filename, so the body only needs editing once.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

TOKEN_FILE = Path.home() / ".confluence_api_token"
BASE_URL = "https://molonc.atlassian.net/wiki/rest/api"


def load_credentials() -> tuple[str, str]:
    if not TOKEN_FILE.exists():
        raise SystemExit(f"Missing {TOKEN_FILE} — see README for setup (classic Atlassian API token).")
    text = TOKEN_FILE.read_text()
    email_match = re.search(r"CONFLUENCE_EMAIL\s*=\s*(.+)", text)
    token_match = re.search(r"CONFLUENCE_API_TOKEN\s*=\s*(.+)", text)
    if not email_match or not token_match:
        raise SystemExit(f"{TOKEN_FILE} must contain CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN lines.")
    return email_match.group(1).strip(), token_match.group(1).strip()


def existing_attachment_id(page_id: str, filename: str, email: str, token: str) -> str | None:
    result = subprocess.run(
        ["curl", "-s", "-u", f"{email}:{token}", f"{BASE_URL}/content/{page_id}/child/attachment"],
        capture_output=True, text=True, check=True,
    )
    response = json.loads(result.stdout)
    for attachment in response.get("results", []):
        if attachment["title"] == filename:
            return attachment["id"]
    return None


def upload_attachment(page_id: str, path: Path, email: str, token: str) -> dict:
    """Create a new attachment, or add a new version if one with this
    filename already exists — Confluence Cloud's create endpoint rejects a
    duplicate filename outright rather than versioning it automatically."""
    attachment_id = existing_attachment_id(page_id, path.name, email, token)
    if attachment_id is None:
        url = f"{BASE_URL}/content/{page_id}/child/attachment"
    else:
        url = f"{BASE_URL}/content/{page_id}/child/attachment/{attachment_id}/data"

    result = subprocess.run(
        [
            "curl", "-s",
            "-u", f"{email}:{token}",
            "-X", "POST",
            "-H", "X-Atlassian-Token: no-check",
            "-F", f"file=@{path};type=image/png",
            "-F", "minorEdit=true",
            url,
        ],
        capture_output=True, text=True, check=True,
    )
    response = json.loads(result.stdout)
    if "results" in response:
        return response["results"][0]
    if "id" in response:  # the .../data update endpoint returns the attachment directly
        return response
    raise RuntimeError(f"Upload of {path.name} failed: {response}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page_id", help="Confluence page ID to attach files to")
    parser.add_argument("png_files", nargs="+", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    email, token = load_credentials()
    for png_path in args.png_files:
        attachment = upload_attachment(args.page_id, png_path, email, token)
        file_id = attachment["extensions"]["fileId"]
        collection = attachment["extensions"]["collectionName"]
        print(f"{png_path.name}: fileId={file_id} collection={collection}")


if __name__ == "__main__":
    main()
