"""用桌面版 Python 引擎生成文本比较的期望结果（仅标准库）。

读取 stdin JSON: {"cases":[{"name":..,"left":..,"right":..,"options":{...}}]}
输出 JSON: {"cases":[{"name":..,"left":{...},"right":{...},"diffs":[...]}]}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from engineerdiff.models import TextCompareOptions  # noqa: E402
from engineerdiff.textdiff.engine import compare_texts  # noqa: E402

OPTION_MAP = {
    "ignoreLineEndings": "ignore_line_endings",
    "ignoreOuterSpace": "ignore_outer_space",
    "ignoreAllSpace": "ignore_all_space",
    "ignoreCase": "ignore_case",
}


def norm_side(side) -> dict:
    return {
        "encoding": side.encoding,
        "lineEnding": side.line_ending,
        "trailingNewline": side.trailing_newline,
        "originalLineCount": side.original_line_count,
        "lines": [
            {
                "text": line.text,
                "lineNo": line.line_no,
                "status": line.status.value,
                "spans": [[span.start, span.end] for span in line.spans],
                "diffId": line.diff_id,
            }
            for line in side.lines
        ],
    }


def norm_diff(diff) -> dict:
    def loc(location):
        if location is None:
            return None
        return {
            "line": location.line,
            "text": location.text,
            "spans": [[int(span[0]), int(span[1])] for span in location.spans],
            "placeholder": bool(location.extra.get("placeholder", False)),
            "displayRow": location.extra.get("display_row"),
        }

    return {
        "id": diff.id,
        "kind": diff.kind.value,
        "title": diff.title,
        "detail": diff.detail,
        "left": loc(diff.left),
        "right": loc(diff.right),
    }


def main() -> int:
    payload = json.load(sys.stdin)
    out = []
    for case in payload["cases"]:
        kwargs = {
            OPTION_MAP[key]: value
            for key, value in (case.get("options") or {}).items()
            if key in OPTION_MAP
        }
        result = compare_texts(case["left"], case["right"], options=TextCompareOptions(**kwargs))
        out.append(
            {
                "name": case["name"],
                "left": norm_side(result.left),
                "right": norm_side(result.right),
                "diffs": [norm_diff(diff) for diff in result.diffs],
            }
        )
    json.dump({"cases": out}, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
