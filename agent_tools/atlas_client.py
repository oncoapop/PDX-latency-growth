#!/usr/bin/env python3
"""Read-only client for the Atlas Transplants table (the authoritative
surgery-date source), used to cross-check the cage-ID YYMMDD heuristic.

Confirmed endpoint (2026-10-30): GET https://atlas-api.molonc.ca/api/2/1/xenodb/transplants/
returns every transplant record in one page (no pagination params needed).
See ~/Documents/Codex/workflows/atlas-api-access.md for how the token in
~/.atlas was obtained and the WAF/User-Agent note.

This module never prints, logs, or writes the token or the raw Atlas payload
to disk; it only returns a small derived cage_id -> surgery_date mapping for
callers to merge into their own (already-local) data. Network/auth failures
are reported as a best-effort "unavailable" result rather than raised, so a
pipeline that calls this stays runnable without Atlas access (same pattern as
plot_tumour_growth_by_cage.py's fetch_xenodb_metadata).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ATLAS_API_BASE = "https://atlas-api.molonc.ca/api/2/1"
TRANSPLANTS_PATH = "/xenodb/transplants/"

# Azure Application Gateway WAF rejects Python's default urllib User-Agent.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    "Accept": "application/json",
}


@dataclass
class CageSurgeryDate:
    surgery_date: str | None  # ISO date string, or None if unavailable/conflicting
    status: str  # "matched" | "conflicting dates in Atlas: ..." | "no surgery_date on record"


@dataclass
class AnimalAtlasRecord:
    """One Atlas transplant record for one animal/site: Atlas's `ep` field
    (ear position: R/L/B/N) matches the site label our own extraction uses,
    so (cage_id, ep) keys this at exactly the same per-animal granularity as
    our own mouse_id = f"{cage} {site}". `patient_id`/`patient_cid` are
    Atlas's own sample identifiers — a different naming scheme from the
    lab's informal "SA###" labels (confirmed no textual overlap), but the
    authoritative identity for this specific animal's implant."""

    patient_id: str
    patient_cid: str
    passage: str | None
    transplant_id: str
    barcode: str
    surgery_date: str | None
    grown: str | None
    necropsy: str | None
    status: str  # "matched" | "conflicting records for this cage+ep: ..."


def load_token(path: str = "~/.atlas") -> str | None:
    expanded = Path(path).expanduser()
    if not expanded.exists():
        return None
    text = expanded.read_text()
    match = re.search(r"ATLAS_TOKEN\s*=\s*['\"]?([^'\"\s]+)", text)
    return match.group(1) if match else None


def fetch_transplants(token: str, timeout: int = 30) -> list[dict[str, Any]]:
    """One GET of the full Transplants table. Raises on any failure; callers
    should catch and degrade gracefully (see build_cage_crosswalk)."""
    headers = {**_HEADERS, "Authorization": f"Bearer {token}"}
    request = urllib.request.Request(ATLAS_API_BASE + TRANSPLANTS_PATH, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode())
    return body.get("data", [])


def build_cage_crosswalk(records: list[dict[str, Any]]) -> dict[str, CageSurgeryDate]:
    """One row per cage_id, aggregating every transplant barcode recorded
    against it. A cage with more than one distinct surgery_date in Atlas is
    reported as conflicting rather than guessed."""
    by_cage: dict[str, set[str]] = {}
    for record in records:
        cage = str(record.get("cage_id") or "").strip()
        surgery_date = record.get("surgery_date")
        if not cage:
            continue
        by_cage.setdefault(cage, set())
        if surgery_date:
            by_cage[cage].add(str(surgery_date))

    crosswalk: dict[str, CageSurgeryDate] = {}
    for cage, dates in by_cage.items():
        if len(dates) == 1:
            crosswalk[cage] = CageSurgeryDate(surgery_date=next(iter(dates)), status="matched")
        elif len(dates) == 0:
            crosswalk[cage] = CageSurgeryDate(surgery_date=None, status="no surgery_date on record")
        else:
            crosswalk[cage] = CageSurgeryDate(
                surgery_date=None,
                status=f"conflicting dates in Atlas: {'; '.join(sorted(dates))}",
            )
    return crosswalk


def fetch_cage_crosswalk(token_path: str = "~/.atlas") -> tuple[dict[str, CageSurgeryDate], str]:
    """Best-effort fetch. Returns (crosswalk, status_message); crosswalk is
    empty if Atlas is unreachable, unauthorized, or no token is configured —
    callers should fall back to the cage-ID heuristic in that case, not fail."""
    token = load_token(token_path)
    if not token:
        return {}, f"No Atlas token found at {token_path}; skipping Atlas cross-check."
    try:
        records = fetch_transplants(token)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as error:
        return {}, f"Atlas unavailable ({error}); skipping Atlas cross-check."
    if not records:
        return {}, "Atlas returned no transplant records; skipping Atlas cross-check."
    return build_cage_crosswalk(records), f"Fetched {len(records)} Atlas transplant record(s) for cross-check."


def build_animal_crosswalk(records: list[dict[str, Any]]) -> dict[str, AnimalAtlasRecord]:
    """One row per (cage_id, ep) — the same per-animal key as mouse_id in
    build_growth_measurements_from_raw.py (f"{cage} {site}"). A cage+ep pair
    with more than one transplant record (e.g. a re-implant) is reported as
    conflicting rather than silently picking one."""
    by_key: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        cage = str(record.get("cage_id") or "").strip()
        ep = str(record.get("ep") or "").strip().upper()
        if not cage or not ep:
            continue
        by_key.setdefault(f"{cage} {ep}", []).append(record)

    crosswalk: dict[str, AnimalAtlasRecord] = {}
    for key, recs in by_key.items():
        if len(recs) > 1:
            barcodes = ", ".join(str(r.get("barcode")) for r in recs)
            crosswalk[key] = AnimalAtlasRecord(
                patient_id="", patient_cid="", passage=None, transplant_id="", barcode="",
                surgery_date=None, grown=None, necropsy=None,
                status=f"conflicting records for this cage+ep: barcodes {barcodes}",
            )
            continue
        record = recs[0]
        crosswalk[key] = AnimalAtlasRecord(
            patient_id=str(record.get("patient_id") or ""),
            patient_cid=str(record.get("patient_cid") or ""),
            passage=str(record["passage"]) if record.get("passage") is not None else None,
            transplant_id=str(record.get("transplant_id") or ""),
            barcode=str(record.get("barcode") or ""),
            surgery_date=record.get("surgery_date"),
            grown=str(record["grown"]) if record.get("grown") is not None else None,
            necropsy=record.get("necropsy"),
            status="matched",
        )
    return crosswalk


def fetch_animal_crosswalk(token_path: str = "~/.atlas") -> tuple[dict[str, AnimalAtlasRecord], str]:
    """Best-effort fetch of the per-animal (cage_id, ep) crosswalk. Same
    degrade-gracefully contract as fetch_cage_crosswalk."""
    token = load_token(token_path)
    if not token:
        return {}, f"No Atlas token found at {token_path}; skipping Atlas animal cross-check."
    try:
        records = fetch_transplants(token)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as error:
        return {}, f"Atlas unavailable ({error}); skipping Atlas animal cross-check."
    if not records:
        return {}, "Atlas returned no transplant records; skipping Atlas animal cross-check."
    return build_animal_crosswalk(records), f"Fetched {len(records)} Atlas transplant record(s) for the per-animal cross-check."


def _load_animal_crosswalk_cache(cache_path: Path) -> tuple[dict[str, AnimalAtlasRecord], str, float] | None:
    try:
        payload = json.loads(cache_path.read_text())
        crosswalk = {key: AnimalAtlasRecord(**entry) for key, entry in payload["crosswalk"].items()}
        return crosswalk, payload["fetched_at_iso"], payload["fetched_at_epoch"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def _save_animal_crosswalk_cache(cache_path: Path, crosswalk: dict[str, AnimalAtlasRecord]) -> None:
    now = time.time()
    payload = {
        "fetched_at_epoch": now,
        "fetched_at_iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "crosswalk": {key: asdict(entry) for key, entry in crosswalk.items()},
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload))


def fetch_animal_crosswalk_cached(
    cache_path: Path,
    max_age_hours: float = 24.0,
    force_refresh: bool = False,
    token_path: str = "~/.atlas",
) -> tuple[dict[str, AnimalAtlasRecord], str]:
    """Cached version of fetch_animal_crosswalk, same reuse/fallback
    semantics as fetch_cage_crosswalk_cached."""
    cached = None if force_refresh else _load_animal_crosswalk_cache(cache_path)
    if cached is not None:
        crosswalk, fetched_at_iso, fetched_at_epoch = cached
        age_hours = (time.time() - fetched_at_epoch) / 3600
        if age_hours <= max_age_hours:
            return crosswalk, f"Using cached Atlas animal cross-check from {fetched_at_iso} ({age_hours:.1f}h old, {len(crosswalk)} animal(s))."

    crosswalk, status = fetch_animal_crosswalk(token_path)
    if crosswalk:
        _save_animal_crosswalk_cache(cache_path, crosswalk)
        return crosswalk, status + " Cached for reuse."

    stale = _load_animal_crosswalk_cache(cache_path)
    if stale is not None:
        crosswalk, fetched_at_iso, _ = stale
        return crosswalk, f"{status} Using stale cached Atlas animal cross-check from {fetched_at_iso} instead."
    return crosswalk, status


def _load_crosswalk_cache(cache_path: Path) -> tuple[dict[str, CageSurgeryDate], str] | None:
    try:
        payload = json.loads(cache_path.read_text())
        crosswalk = {cage: CageSurgeryDate(**entry) for cage, entry in payload["crosswalk"].items()}
        return crosswalk, payload["fetched_at_iso"], payload["fetched_at_epoch"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def _save_crosswalk_cache(cache_path: Path, crosswalk: dict[str, CageSurgeryDate]) -> None:
    now = time.time()
    payload = {
        "fetched_at_epoch": now,
        "fetched_at_iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        # Only the small, derived cage_id -> surgery_date mapping is cached —
        # never the raw per-patient Atlas records.
        "crosswalk": {cage: asdict(entry) for cage, entry in crosswalk.items()},
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload))


def fetch_cage_crosswalk_cached(
    cache_path: Path,
    max_age_hours: float = 24.0,
    force_refresh: bool = False,
    token_path: str = "~/.atlas",
) -> tuple[dict[str, CageSurgeryDate], str]:
    """Like fetch_cage_crosswalk, but reuses a local cache of the small
    derived cage_id -> surgery_date mapping (never the raw Atlas payload)
    when it is younger than max_age_hours, so a routine re-run doesn't hit
    the API again every time. Pass force_refresh=True to always fetch fresh
    (e.g. a --refresh-atlas CLI flag)."""
    cached = None if force_refresh else _load_crosswalk_cache(cache_path)
    if cached is not None:
        crosswalk, fetched_at_iso, fetched_at_epoch = cached
        age_hours = (time.time() - fetched_at_epoch) / 3600
        if age_hours <= max_age_hours:
            return crosswalk, f"Using cached Atlas cross-check from {fetched_at_iso} ({age_hours:.1f}h old, {len(crosswalk)} cage(s))."

    crosswalk, status = fetch_cage_crosswalk(token_path)
    if crosswalk:
        _save_crosswalk_cache(cache_path, crosswalk)
        return crosswalk, status + " Cached for reuse."

    # Fetch failed or returned nothing — fall back to a stale cache rather
    # than silently dropping to the cage-ID heuristic for everything.
    stale = _load_crosswalk_cache(cache_path)
    if stale is not None:
        crosswalk, fetched_at_iso, _ = stale
        return crosswalk, f"{status} Using stale cached Atlas cross-check from {fetched_at_iso} instead."
    return crosswalk, status
