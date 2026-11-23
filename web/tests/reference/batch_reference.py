"""用桌面版批量引擎生成期望结果（TXT / CSV / XLSX）。

stdin: {"cases":[{"name":..,"baseline":path,"candidates":[path...],
                  "text":{...},"sheet":{...}}]}
stdout: {"cases":[{"name":..,"mode","rows":[...],"differentCount":..,"fileCount":..,
                   "candidates":[{"name","diffCount","error"}]}]}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from engineerdiff.batch import run_batch  # noqa: E402
from engineerdiff.models import AppSettings, TextCompareOptions, SpreadsheetOptions  # noqa: E402


def norm_row(row) -> dict:
    return {
        "location": row.location,
        "baseline": row.baseline,
        "values": list(row.values),
        "differs": list(row.differs),
        "severity": row.severity.value,
        "sheet": row.sheet,
        "note": row.note,
    }


def main() -> int:
    payload = json.load(sys.stdin)
    out = []
    for case in payload["cases"]:
        settings = AppSettings()
        text = case.get("text") or {}
        settings.text = TextCompareOptions(
            ignore_line_endings=bool(text.get("ignoreLineEndings", False)),
            ignore_outer_space=bool(text.get("ignoreOuterSpace", False)),
            ignore_all_space=bool(text.get("ignoreAllSpace", False)),
            ignore_case=bool(text.get("ignoreCase", False)),
        )
        sheet = case.get("sheet") or {}
        settings.spreadsheet = SpreadsheetOptions(
            formula_mode=sheet.get("formulaMode", "formula"),
            numeric_tolerance=float(sheet.get("numericTolerance", 0)),
            smart_rows=bool(sheet.get("smartRows", False)),
        )
        batch = run_batch(case["baseline"], case["candidates"], settings)
        out.append(
            {
                "name": case["name"],
                "mode": batch.mode.value,
                "rows": [norm_row(row) for row in batch.rows],
                "differentCount": batch.different_count,
                "fileCount": batch.file_count,
                "candidates": [
                    {"name": cand.name, "diffCount": cand.diff_count, "error": cand.error}
                    for cand in batch.candidates
                ],
            }
        )
    json.dump({"cases": out}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
