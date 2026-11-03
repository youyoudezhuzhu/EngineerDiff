"""批量比较：一个基准文件 + 多个候选文件。

差异采用“或”语义：对基准中的同一位置，只要有任意一个候选文件与基准不同，
该位置就进入批量差异表并标红。批量表同时给出每个候选在该位置的值，便于一次
看出“哪个文件在哪一处不一样”。

实现方式：以基准文件为中心，依次调用现有的单对比较引擎；这样文本、CSV、Excel、
图片 OCR 的严格/容差/公式/OCR 匹配策略与普通比较完全一致，不存在第二套比较规则。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .compare import ComparisonCancelled, Progress, run_comparison
from .file_loader import FileKind, classify, determine_mode, mode_label
from .models import (
    AppSettings,
    CompareMode,
    ComparisonResult,
    DiffItem,
    Severity,
)
from .spreadsheet.loader import column_letter


class BatchError(ValueError):
    """批量比较参数/文件错误，消息可直接展示。"""


@dataclass
class BatchCandidate:
    path: str
    result: ComparisonResult | None = None
    error: str = ""
    diff_count: int = 0

    @property
    def name(self) -> str:
        return Path(self.path).name


@dataclass
class BatchRow:
    key: tuple
    location: str
    baseline: str
    values: list[str]
    differs: list[bool]
    severity: Severity = Severity.CONFIRMED
    note: str = ""
    sort_key: tuple = ()
    sheet: str = ""
    image_diff: DiffItem | None = None
    image_candidate: int = -1


@dataclass
class BatchResult:
    mode: CompareMode
    baseline: str
    candidates: list[BatchCandidate]
    rows: list[BatchRow]
    warnings: list[str] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def different_count(self) -> int:
        return len(self.rows)

    @property
    def file_count(self) -> int:
        return len(self.candidates) + 1


def _file_group(path: str) -> str:
    kind = classify(path).kind
    if kind == FileKind.TEXT:
        return "text"
    if kind == FileKind.CSV:
        return "csv"
    if kind in (FileKind.XLS, FileKind.XLSX):
        return "excel"
    if kind == FileKind.IMAGE:
        return "image"
    raise BatchError(f"不支持的文件类型：{path}")


def _mode_for_group(group: str) -> CompareMode:
    return {
        "text": CompareMode.TEXT,
        "csv": CompareMode.CSV,
        "excel": CompareMode.EXCEL,
        "image": CompareMode.IMAGE,
    }[group]


def _diff_items(result: ComparisonResult) -> list[DiffItem]:
    if result.mode == CompareMode.TEXT and result.text is not None:
        return list(result.text.diffs)
    if result.mode == CompareMode.CSV and result.csv is not None:
        return list(result.csv.table.diffs)
    if result.mode == CompareMode.EXCEL and result.workbook is not None:
        return list(result.workbook.diffs)
    if result.mode == CompareMode.IMAGE and result.image is not None:
        return list(result.image.diffs)
    return []


def _cell_location(sheet: str | None, row: int | None, col: int | None) -> str:
    if row is None or col is None:
        return sheet or "单元格"
    label = f"{column_letter(col)}{row + 1}"
    return f"{sheet}!{label}" if sheet else label


def _blank_text(text: str) -> str:
    return "（空白）" if text == "" else text


def _right_value(diff: DiffItem) -> str:
    if diff.right is None or diff.right.extra.get("placeholder"):
        return "（不存在）"
    return _blank_text(diff.right.text)


def _left_value(diff: DiffItem) -> str:
    if diff.left is None or diff.left.extra.get("placeholder"):
        return "—"
    return _blank_text(diff.left.text)


def _diff_key_and_location(diff: DiffItem, cand_index: int) -> tuple[tuple, str, bool, tuple]:
    """返回 (合并键, 位置文本, 是否有基准位置, 排序键)。"""
    # 文本行
    if diff.group == "text":
        if diff.title == "换行符差异":
            return ("eol",), "换行符", True, (10**9, 0)
        left_line = diff.left.line if diff.left is not None else None
        right_line = diff.right.line if diff.right is not None else None
        if left_line is not None:
            return ("text", left_line), f"第 {left_line} 行", True, (left_line, 0)
        if right_line is not None:
            return (
                ("text-add", cand_index, right_line),
                f"第 {right_line} 行（仅候选新增）",
                False,
                (right_line, 1),
            )

    # 工作表级
    if diff.group == "sheet" or diff.kind.value in ("only_left", "only_right"):
        loc = diff.left if diff.left is not None else diff.right
        sheet = loc.sheet if loc is not None else ""
        if sheet:
            return ("sheet", sheet), f"工作表「{sheet}」", diff.left is not None, (0, 0, 0, sheet)

    # 单元格
    if diff.group == "cell":
        loc = diff.left if diff.left is not None else diff.right
        if loc is not None and loc.row is not None and loc.col is not None:
            if diff.left is not None:
                return (
                    ("cell", loc.sheet, loc.row, loc.col),
                    _cell_location(loc.sheet, loc.row, loc.col),
                    True,
                    (1, loc.sheet or "", loc.row, loc.col),
                )
            return (
                ("cell-add", cand_index, loc.sheet, loc.row, loc.col),
                f"{_cell_location(loc.sheet, loc.row, loc.col)}（仅候选）",
                False,
                (1, loc.sheet or "", loc.row, loc.col),
            )

    # 图片文字框
    if diff.group == "image":
        if diff.left is not None and diff.left.bbox is not None:
            b = diff.left.bbox
            return (
                ("img", round(b.x / 8), round(b.y / 8), diff.left.text),
                f"文字 ({int(b.x)}, {int(b.y)})",
                True,
                (2, b.y, b.x),
            )
        if diff.right is not None and diff.right.bbox is not None:
            b = diff.right.bbox
            return (
                ("img-add", cand_index, round(b.x / 8), round(b.y / 8)),
                f"文字 ({int(b.x)}, {int(b.y)})（仅候选）",
                False,
                (2, b.y, b.x),
            )

    return ("other", cand_index, diff.id), diff.title or "差异", False, (9, 0)


def build_batch_result(
    baseline: str,
    candidates: list[BatchCandidate],
    mode: CompareMode,
) -> BatchResult:
    """把多次单对比较结果合并成批量差异表（或语义）。"""
    n = len(candidates)
    rows: dict[tuple, BatchRow] = {}
    warnings: list[str] = []

    for index, cand in enumerate(candidates):
        if cand.error:
            warnings.append(f"{cand.name}：{cand.error}")
            continue
        assert cand.result is not None
        for diff in _diff_items(cand.result):
            key, location, has_base, sort_key = _diff_key_and_location(diff, index)
            loc = diff.left if diff.left is not None else diff.right
            sheet_name = loc.sheet if loc is not None and loc.sheet else ""
            row = rows.get(key)
            if row is None:
                row = BatchRow(
                    key=key,
                    location=location,
                    baseline=_left_value(diff),
                    values=["—"] * n,
                    differs=[False] * n,
                    severity=diff.severity if diff.is_uncertain else Severity.CONFIRMED,
                    note=diff.detail,
                    sort_key=sort_key,
                    sheet=sheet_name,
                )
                rows[key] = row
            elif not row.baseline or row.baseline == "—":
                if diff.left is not None:
                    row.baseline = _left_value(diff)
            if not row.sheet and sheet_name:
                row.sheet = sheet_name
            if row.image_diff is None and diff.group == "image":
                row.image_diff = diff
                row.image_candidate = index
            row.values[index] = _right_value(diff)
            row.differs[index] = True
            if diff.is_uncertain:
                if row.severity != Severity.CONFIRMED:
                    row.severity = Severity.UNCERTAIN
            else:
                row.severity = Severity.CONFIRMED
            if not row.note and diff.detail:
                row.note = diff.detail

    # 没有差异的候选在已有基准行的值 = 基准值；仅候选新增行只保留实际出现的值。
    for row in rows.values():
        has_base = row.baseline != "—"
        for i in range(n):
            if row.differs[i]:
                continue
            row.values[i] = row.baseline if has_base else "—"

    ordered = sorted(rows.values(), key=lambda r: (r.sort_key, r.location))
    warnings = list(dict.fromkeys(warnings))
    return BatchResult(
        mode=mode,
        baseline=baseline,
        candidates=candidates,
        rows=ordered,
        warnings=warnings,
    )


def run_batch(
    baseline: str,
    candidates: list[str],
    settings: AppSettings | None = None,
    progress: Progress | None = None,
    cancel: Callable[[], bool] | None = None,
    ocr_service=None,
    on_candidate: Callable[[int, "BatchCandidate"], None] | None = None,
) -> BatchResult:
    """批量比较入口。candidates 不含基准；返回按位置合并的或语义差异表。"""
    if not candidates:
        raise BatchError("请至少添加一个待比较文件。")
    baseline_info = classify(baseline)
    if not baseline_info.exists:
        raise BatchError(f"基准文件不存在：{baseline}")
    groups = {_file_group(baseline)}
    for path in candidates:
        info = classify(path)
        if not info.exists:
            raise BatchError(f"候选文件不存在：{path}")
        groups.add(_file_group(path))
    if len(groups) > 1:
        raise BatchError(
            "批量比较要求所有文件类型一致（TXT / CSV / XLSX / XLS / 图片不能混合）。"
            f"当前类型：{', '.join(sorted(groups))}"
        )
    group = next(iter(groups))
    mode = _mode_for_group(group)
    # 逐对调用正式比较引擎，确保与普通比较结果一致。
    for path in candidates:
        try:
            determine_mode(baseline, path)
        except Exception as exc:
            raise BatchError(str(exc)) from exc

    settings = settings or AppSettings()
    progress = progress or (lambda _p, _m: None)
    cancel = cancel or (lambda: False)
    if mode == CompareMode.IMAGE and ocr_service is None:
        from .ocr.engine import OcrService

        ocr_service = OcrService()
    started = time.time()
    results: list[BatchCandidate] = []
    total = len(candidates)
    for index, path in enumerate(candidates):
        if cancel():
            raise ComparisonCancelled("用户取消了批量比较")
        progress(5 + int(index * 85 / total), f"正在比较 {index + 1}/{total}：{Path(path).name}")
        try:
            result = run_comparison(
                mode,
                baseline,
                path,
                settings,
                progress=lambda p, m, idx=index: progress(
                    5 + int((idx + max(0, min(100, p)) / 100.0) * 85 / total),
                    f"文件 {idx + 1}/{total}：{m}",
                ),
                cancel=cancel,
                ocr_service=ocr_service,
            )
            diff_items = _diff_items(result)
            candidate = BatchCandidate(path, result, "", len(diff_items))
            results.append(candidate)
        except ComparisonCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - 单个候选失败不应中断整批
            candidate = BatchCandidate(path, None, f"{type(exc).__name__}: {exc}", 0)
            results.append(candidate)
        if on_candidate is not None:
            try:
                on_candidate(len(results) - 1, candidate)
            except Exception:
                pass

    batch = build_batch_result(baseline, results, mode)
    extra_warnings: list[str] = []
    for cand in results:
        if cand.result is not None:
            for warning in cand.result.warnings:
                extra_warnings.append(f"{cand.name}：{warning}")
    batch.warnings = list(dict.fromkeys(batch.warnings + extra_warnings))
    batch.elapsed = time.time() - started
    progress(100, f"批量比较完成：{batch.file_count} 个文件，{batch.different_count} 处差异")
    return batch
