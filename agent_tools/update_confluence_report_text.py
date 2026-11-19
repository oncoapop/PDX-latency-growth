#!/usr/bin/env python3
"""Update the "Weekly Living-Mouse Tumour Growth Status" Confluence page's
TEXT (last-updated date, summary counts, both tables) via direct REST calls
-- no Atlassian MCP connector needed, so this can run from a standalone
process (e.g. the Slack bot) with no Claude Code agent involved.

Uses the same ~/.confluence_api_token credentials as
post_weekly_report_to_confluence.py. Reads the current body first (required
for a safe update), applies only well-anchored, narrow substitutions, and
ABORTS without writing if any expected anchor isn't found -- never guess or
partial-write a shared page.

Image embeds use <ac:image ac:alt="FILENAME"><ri:attachment
ri:filename="FILENAME" ri:version-at-save="N" /></ac:image> -- referenced by
FILENAME, not a fileId. post_weekly_report_to_confluence.py already uploads
a new version under the same filename, so the embed just needs
ri:version-at-save REMOVED (not updated) so it always shows the latest
upload automatically -- this script does that once; no per-run fileId
tracking needed at all, unlike the old assumption.

Deliberately specific to this one page's known template structure (not a
general Confluence editor). The manually-added note paragraph after the
"Automation" section, and everything else on the page, is left untouched:
this script only touches the three regions listed above (last-updated date,
summary list, the two data tables) plus the one-time image-embed fix.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from datetime import date, datetime
from pathlib import Path

TOKEN_FILE = Path.home() / ".confluence_api_token"
BASE_URL = "https://molonc.atlassian.net/wiki/rest/api"


def load_credentials() -> tuple[str, str]:
    if not TOKEN_FILE.exists():
        raise SystemExit(f"Missing {TOKEN_FILE}")
    text = TOKEN_FILE.read_text()
    email_match = re.search(r"CONFLUENCE_EMAIL\s*=\s*(.+)", text)
    token_match = re.search(r"CONFLUENCE_API_TOKEN\s*=\s*(.+)", text)
    if not email_match or not token_match:
        raise SystemExit(f"{TOKEN_FILE} must contain CONFLUENCE_EMAIL and CONFLUENCE_API_TOKEN lines.")
    return email_match.group(1).strip(), token_match.group(1).strip()


def get_page(page_id: str, email: str, token: str) -> dict:
    result = subprocess.run(
        ["curl", "-s", "-u", f"{email}:{token}",
         f"{BASE_URL}/content/{page_id}?expand=body.storage,version"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def put_page(page_id: str, email: str, token: str, title: str, new_version: int, body_html: str, message: str) -> dict:
    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "version": {"number": new_version, "message": message},
        "body": {"storage": {"value": body_html, "representation": "storage"}},
    }
    result = subprocess.run(
        ["curl", "-s", "-u", f"{email}:{token}",
         "-X", "PUT", "-H", "Content-Type: application/json",
         "-d", json.dumps(payload),
         f"{BASE_URL}/content/{page_id}"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_summary_ul(n_living: int, n_cages: int, n_flagged: int, n_near_term: int, near_term_dates: list[str]) -> str:
    near = (
        f"; {n_near_term} due within the next 14 days"
        + (f" (boxed in red on the plot below: {', '.join(near_term_dates)})" if near_term_dates else "")
        if n_near_term else ""
    )
    flagged_line = (
        f"{n_flagged} flagged: no tumour &ge;100 mm3 and more than 90 days since surgery"
        if n_flagged else "0 flagged: all living mice have reached 100 mm3 within 90 days of surgery"
    )
    living_line = f"{n_living} living mice across {n_cages} cages"
    proj_line = f"actively-growing mice above 200 mm3 with a current harvest-date projection{near}"
    return (
        "<ul>"
        f"<li><p>{living_line}</p></li>"
        f"<li><p>{flagged_line}</p></li>"
        f"<li><p>{proj_line}</p></li>"
        "</ul>"
    )


def build_flagged_table(rows: list[dict]) -> str:
    header = (
        "<tr><th><p>Mouse</p></th><th><p>Cage</p></th><th><p>SA ID / passage</p></th>"
        "<th><p>Surgery date</p></th><th><p>Days since surgery</p></th><th><p>Max volume recorded (mm3)</p></th></tr>"
    )
    if not rows:
        body = '<tr><td colspan="6"><p>None flagged this week.</p></td></tr>'
    else:
        trs = []
        for r in rows:
            passage = str(int(float(r["passage"]))) if r.get("passage") else ""
            trs.append(
                "<tr>"
                f"<td><p>{esc(r['mouse_id'])}</p></td>"
                f"<td><p>{esc(r['cage'])}</p></td>"
                f"<td><p>{esc(r['sa_id'])} / {passage}</p></td>"
                f"<td><p>{esc(r['surgery_date'][:10])}</p></td>"
                f"<td><p>{esc(r['days_since_surgery'])}</p></td>"
                f"<td><p>{float(r['max_volume_mm3']):.1f}</p></td>"
                "</tr>"
            )
        body = "".join(trs)
    return f'<table data-table-width="760" data-layout="default"><tbody>{header}{body}</tbody></table>'


def fmt_proj_cell(date_str: str, status: str, bold: bool) -> str:
    if not date_str:
        text = "n/a"
    elif status == "observed":
        text = "observed"
    else:
        text = f"projected ~{date_str}"
    inner = f"<strong>{esc(text)}</strong>" if bold else esc(text)
    return f"<td><p>{inner}</p></td>"


def build_projection_table(rows: list[dict], as_of: date) -> tuple[str, int, list[str]]:
    header = (
        "<tr><th><p>Cage</p></th><th><p>SA ID</p></th><th><p>Mouse / site</p></th><th><p>Transplant date</p></th>"
        "<th><p>Latest volume (mm3)</p></th><th><p>500 mm3</p></th><th><p>1000 mm3</p></th></tr>"
    )
    trs = []
    n_near_term = 0
    near_term_dates = []
    for r in rows:
        d500, s500 = r.get("date_500_mm3", ""), r.get("date_500_status", "")
        d1000, s1000 = r.get("date_1000_mm3", ""), r.get("date_1000_status", "")
        bold1000 = False
        if d1000 and s1000 == "projected":
            try:
                dd = datetime.strptime(d1000, "%Y-%m-%d").date()
                days_until = (dd - as_of).days
                if 0 <= days_until <= 14:
                    bold1000 = True
                    n_near_term += 1
                    near_term_dates.append(dd.strftime("%-d %b"))
            except ValueError:
                pass
        trs.append(
            "<tr>"
            f"<td><p>{esc(r['cage'])}</p></td>"
            f"<td><p>{esc(r['tumour_model'])}</p></td>"
            f"<td><p>{esc(r['mouse_id'])}</p></td>"
            f"<td><p>{esc(r['transplant_date'][:10])}</p></td>"
            f"<td><p>{float(r['latest_volume_mm3']):.0f}</p></td>"
            + fmt_proj_cell(d500, s500, False)
            + fmt_proj_cell(d1000, s1000, bold1000)
            + "</tr>"
        )
    body = "".join(trs) if trs else '<tr><td colspan="7"><p>No currently-growing traces above the projection floor.</p></td></tr>'
    return f'<table data-table-width="760" data-layout="default"><tbody>{header}{body}</tbody></table>', n_near_term, near_term_dates


def replace_anchored(body: str, pattern: str, replacement: str, label: str, expected: int = 1) -> str:
    matches = list(re.finditer(pattern, body, flags=re.DOTALL))
    if len(matches) != expected:
        raise RuntimeError(
            f"Anchor mismatch for: {label} (expected {expected} match(es), got {len(matches)}) -- aborting without writing"
        )
    return re.sub(pattern, lambda m: replacement.replace("\\", "\\\\"), body, count=expected, flags=re.DOTALL)


def strip_version_pin(body: str, filename: str) -> str:
    pattern = rf'(<ri:attachment ri:filename="{re.escape(filename)}")( ri:version-at-save="\d+")?( */>)'
    matches = list(re.finditer(pattern, body))
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly 1 ri:attachment for {filename}, found {len(matches)} -- aborting without writing")
    return re.sub(pattern, r"\1\3", body, count=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page_id")
    parser.add_argument("--n-living", type=int, required=True)
    parser.add_argument("--n-cages", type=int, required=True)
    parser.add_argument("--flagged-csv", type=Path, required=True)
    parser.add_argument("--projection-csv", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="Print the new body instead of writing it")
    args = parser.parse_args()

    email, token = load_credentials()
    page = get_page(args.page_id, email, token)
    body = page["body"]["storage"]["value"]
    version = page["version"]["number"]
    title = page["title"]

    with open(args.flagged_csv) as f:
        flagged_rows = list(csv.DictReader(f))
    with open(args.projection_csv) as f:
        proj_rows = list(csv.DictReader(f))

    as_of_str = proj_rows[0]["analysis_generated_date"] if proj_rows else date.today().isoformat()
    as_of = datetime.strptime(as_of_str, "%Y-%m-%d").date()

    projection_table_html, n_near_term, near_term_dates = build_projection_table(proj_rows, as_of)
    flagged_table_html = build_flagged_table(flagged_rows)
    summary_ul_html = build_summary_ul(args.n_living, args.n_cages, len(flagged_rows), n_near_term, near_term_dates)
    today_iso = date.today().isoformat()

    body = replace_anchored(
        body, r'<time datetime="[^"]*"( local-id="[^"]*")?\s*/>',
        f'<time datetime="{today_iso}" />',
        "Last updated <time> tag",
    )
    body = replace_anchored(body, r"<h2>Summary</h2><ul>.*?</ul>",
                             f"<h2>Summary</h2>{summary_ul_html}", "Summary section")

    table_matches = list(re.finditer(
        r'<table data-table-width="760" data-layout="default">.*?</table>', body, flags=re.DOTALL
    ))
    if len(table_matches) != 2:
        raise RuntimeError(f"Expected exactly 2 data tables, found {len(table_matches)} -- aborting without writing")
    # Replace second match first so the first match's span is still valid.
    body = body[:table_matches[1].start()] + projection_table_html + body[table_matches[1].end():]
    body = body[:table_matches[0].start()] + flagged_table_html + body[table_matches[0].end():]

    body = strip_version_pin(body, "living_mice_growth_latest.png")
    body = strip_version_pin(body, "pdx_growth_projection_latest.png")

    if args.dry_run:
        print(body)
        return

    result = put_page(args.page_id, email, token, title, version + 1, body, f"Automated weekly update ({today_iso})")
    if "id" not in result:
        raise SystemExit(f"Update failed: {result}")
    print(f"Updated page {args.page_id} to version {result['version']['number']}")


if __name__ == "__main__":
    main()
