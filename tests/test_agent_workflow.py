import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from docx import Document
from openpyxl import Workbook


REPOSITORY = Path(__file__).resolve().parents[1]
AGENT_TOOLS = REPOSITORY / "agent_tools"


class AgentWorkflowTests(unittest.TestCase):
    def run_script(self, name: str, workspace: Path, *extra: Path) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["MPLCONFIGDIR"] = str(workspace / "matplotlib")
        return subprocess.run(
            [sys.executable, str(AGENT_TOOLS / name), str(workspace), *(str(path) for path in extra)],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_copy_parse_rank_and_plot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            workspace = root / "workspace"
            source.mkdir()

            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Cage 123"
            worksheet.append(["SA ID", "Passage", "Volume"])
            worksheet.append(["SA99", "P1", 125])
            workbook.save(source / "Tumor Volumes(2026.01.01).xlsx")

            document = Document()
            document.add_paragraph("ATLAS inventory")
            table = document.add_table(rows=2, cols=3)
            table.cell(0, 0).text = "SA ID"
            table.cell(0, 1).text = "Passage"
            table.cell(0, 2).text = "Material"
            table.cell(1, 0).text = "SA99"
            table.cell(1, 1).text = "P1"
            table.cell(1, 2).text = "VF"
            document.save(source / "atlas.docx")

            self.run_script("prepare_workspace.py", workspace, source)
            self.run_script("parse_inputs.py", workspace)

            manifest = pd.read_csv(workspace / "manifests" / "source_manifest.csv")
            elements = pd.read_csv(workspace / "parsed" / "source_elements.csv")
            self.assertEqual(len(manifest), 2)
            self.assertEqual(set(elements["source_type"]), {"excel_cell", "word_paragraph", "word_table_cell"})
            self.assertIn("SA99", set(elements["value"].astype(str)))

            normalized = workspace / "normalized"
            normalized.mkdir()
            pd.DataFrame(
                [
                    ["SA99", "P1", "cage1 L", "2026-01-01", "2026-01-11", 50],
                    ["SA99", "P1", "cage1 L", "2026-01-01", "2026-01-21", 200],
                    ["SA99", "P1", "cage1 R", "2026-01-01", "2026-01-16", 50],
                    ["SA99", "P1", "cage1 R", "2026-01-01", "2026-01-26", 200],
                ],
                columns=["sa_id", "passage", "mouse_id", "surgery_date", "measurement_date", "tumour_volume_mm3"],
            ).to_csv(normalized / "growth_measurements.csv", index=False)
            pd.DataFrame(
                [["SA99", "P1", "s1", 1], ["SA99", "P1", "s2", 1], ["SA99", "P1", "s3", 0]],
                columns=["sa_id", "passage", "surgery_id", "tumour_taken"],
            ).to_csv(normalized / "surgeries.csv", index=False)
            pd.DataFrame(
                [["SA99", "P1", "Freezer-A1", "VF"]],
                columns=["sa_id", "passage", "atlas_location", "material_label"],
            ).to_csv(normalized / "atlas_inventory.csv", index=False)

            self.run_script("rank_candidates.py", workspace)
            self.run_script("plot_candidate_growth.py", workspace)

            ranking = pd.read_csv(workspace / "analysis" / "eligible_candidate_ranking.csv")
            self.assertEqual(len(ranking), 1)
            self.assertEqual(ranking.iloc[0]["sa_id"], "SA99")
            self.assertTrue(bool(ranking.iloc[0]["recommended_for_growth"]))
            self.assertGreater((workspace / "analysis" / "eligible_candidate_growth_curves.pdf").stat().st_size, 0)
            self.assertEqual(len(list((workspace / "analysis" / "growth_curves").glob("*.png"))), 1)


if __name__ == "__main__":
    unittest.main()
