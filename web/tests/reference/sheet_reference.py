"""用桌面版 Python 引擎生成表格比较期望结果。

stdin: {"cases":[{"name":..,"left":path,"right":path,"options":{"formulaMode":..,"numericTolerance":..,"smartRows":..}}]}
stdout: {"cases":[{"name":..,"diffs":[...],"sheets":[{"name","status"}]}]}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from engineerdiff.models import SpreadsheetOptions  # noqa: E402
from engineerdiff.spreadsheet import load_workbook  # noqa: E402
from engineerdiff.spreadsheet.engine import compare_workbooks  # noqa: E402
from engineerdiff.spreadsheet.csv_parser import parse_csv, csv_to_workbook  # noqa: E402


def norm_location(location):
    if location is None:
        return None
    return {
        "sheet": location.sheet,
        "row": location.row,
        "col": location.col,
        "text": location.text,
        "kind": location.kind,
    }


def norm_diff(diff):
    return {
        "id": diff.id,
        "kind": diff.kind.value,
        "severity": diff.severity.value,
        "title": diff.title,
        "detail": diff.detail,
        "left": norm_location(diff.left),
        "right": norm_location(diff.right),
    }


def load(path: str):
    if path.lower().endswith(".csv"):
        sheet, delimiter, warnings = parse_csv(path)
        return csv_to_workbook(sheet, path, warnings), delimiter
    return load_workbook(path), ","


def main() -> int:
    payload = json.load(sys.stdin)
    out = []
    for case in payload["cases"]:
        left, _ = load(case["left"])
        right, _ = load(case["right"])
        options = SpreadsheetOptions(
            formula_mode=case.get("options", {}).get("formulaMode", "formula"),
            numeric_tolerance=float(case.get("options", {}).get("numericTolerance", 0)),
            smart_rows=bool(case.get("options", {}).get("smartRows", False)),
        )
        comparison = compare_workbooks(left, right, options)
        out.append(
            {
                "name": case["name"],
                "diffs": [norm_diff(diff) for diff in comparison.diffs],
                "sheets": [
                    {"name": sheet.name, "status": sheet.status.value}
                    for sheet in comparison.sheets
                ],
            }
        )
    json.dump({"cases": out}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
