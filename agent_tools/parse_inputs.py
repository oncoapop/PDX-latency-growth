#!/usr/bin/env python3
"""Extract Excel/CSV/Word content with source locations for agent review."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import pandas as pd
from docx import Document
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


def value_text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    return str(value)


def parse_excel(path: Path, workspace: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    workbook = load_workbook(path, read_only=True, data_only=True)
    for sheet in workbook.worksheets:
        for row_number, cells in enumerate(sheet.iter_rows(), start=1):
            for column_number, cell in enumerate(cells, start=1):
                if cell.value is None:
                    continue
                rows.append(
                    {
                        "source_file": str(path.relative_to(workspace)),
                        "source_type": "excel_cell",
                        "container": sheet.title,
                        "location": f"{get_column_letter(column_number)}{row_number}",
                        "value": value_text(cell.value),
                    }
                )
    workbook.close()
    return rows


def parse_delimited(path: Path, workspace: Path) -> list[dict[str, Any]]:
    separator = "\t" if path.suffix.lower() == ".tsv" else ","
    frame = pd.read_csv(path, sep=separator, dtype=str, keep_default_na=False)
    rows: list[dict[str, Any]] = []
    for row_index, record in frame.iterrows():
        for column in frame.columns:
            value = record[column]
            if value == "":
                continue
            rows.append(
                {
                    "source_file": str(path.relative_to(workspace)),
                    "source_type": "delimited_cell",
                    "container": path.name,
                    "location": f"row {row_index + 2}, column {column}",
                    "value": value,
                }
            )
    return rows


def parse_word(path: Path, workspace: Path) -> list[dict[str, Any]]:
    document = Document(path)
    rows: list[dict[str, Any]] = []
    relative = str(path.relative_to(workspace))
    for index, paragraph in enumerate(document.paragraphs, start=1):
        text = paragraph.text.strip()
        if text:
            rows.append(
                {
                    "source_file": relative,
                    "source_type": "word_paragraph",
                    "container": "document body",
                    "location": f"paragraph {index}",
                    "value": text,
                }
            )
    for table_index, table in enumerate(document.tables, start=1):
        for row_index, table_row in enumerate(table.rows, start=1):
            for column_index, cell in enumerate(table_row.cells, start=1):
                text = "\n".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
                if text:
                    rows.append(
                        {
                            "source_file": relative,
                            "source_type": "word_table_cell",
                            "container": f"table {table_index}",
                            "location": f"row {row_index}, column {column_index}",
                            "value": text,
                        }
                    )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    return parser.parse_args()


def main() -> int:
    workspace = parse_args().workspace.expanduser().resolve()
    input_dir = workspace / "input"
    output_dir = workspace / "parsed"
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file() or path.name.startswith("~$"):
            continue
        suffix = path.suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            records.extend(parse_excel(path, workspace))
        elif suffix in {".csv", ".tsv"}:
            records.extend(parse_delimited(path, workspace))
        elif suffix == ".docx":
            records.extend(parse_word(path, workspace))

    output = output_dir / "source_elements.csv"
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source_file", "source_type", "container", "location", "value"],
        )
        writer.writeheader()
        writer.writerows(records)
    print(f"Extracted {len(records)} populated element(s)")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
