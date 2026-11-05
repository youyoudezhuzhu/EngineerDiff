"""表格读取 / 比较 / 自绘渲染测试。

运行：``.venv/bin/python -m pytest tests/test_spreadsheet.py``
GUI 测试使用 Qt offscreen 平台，截图输出到 ``tests/fixtures/generated/screenshots/``
（已在 .gitignore 中忽略）。
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(TESTS_DIR))

from fixtures.generate_spreadsheet_fixtures import ensure_fixtures, generate_all  # noqa: E402

FIXTURES = ensure_fixtures(TESTS_DIR / "fixtures")
SHOT_DIR = TESTS_DIR / "fixtures" / "generated" / "screenshots"

from engineerdiff.models import (  # noqa: E402
    Cell,
    CellType,
    DiffKind,
    Severity,
    Sheet,
    SheetStatus,
    SpreadsheetOptions,
    Workbook,
)
from engineerdiff.spreadsheet import (  # noqa: E402
    SpreadsheetLoadError,
    compare_workbooks,
    load_workbook,
)
from engineerdiff.ui.grid_view import GridView  # noqa: E402
from engineerdiff.ui.spreadsheet_side import SpreadsheetSide  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _cell(value) -> Cell:
    if isinstance(value, Cell):
        return value
    if value is None:
        return Cell()
    if isinstance(value, bool):
        return Cell(display="TRUE" if value else "FALSE", type=CellType.BOOL, value=value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        if isinstance(value, _dt.datetime):
            display = value.strftime("%Y-%m-%d %H:%M:%S") if value.time() != _dt.time(0, 0) else value.strftime("%Y-%m-%d")
        else:
            display = value.strftime("%Y-%m-%d")
        return Cell(display=display, type=CellType.DATE, value=value)
    if isinstance(value, (int, float)):
        return Cell(display=str(value), type=CellType.NUMBER, value=value)
    return Cell(display=str(value), type=CellType.TEXT, value=value)


def _sheet(name: str, rows: list[list]) -> Sheet:
    ncols = max((len(row) for row in rows), default=0)
    data = [[_cell(row[col]) if col < len(row) else Cell() for col in range(ncols)] for row in rows]
    return Sheet(name=name, rows=data, nrows=len(data), ncols=ncols)


def _workbook(*sheets: Sheet, fmt: str = "xlsx") -> Workbook:
    return Workbook(path="memory", fmt=fmt, sheets=list(sheets))


def _formula(formula: str, cached, display: str | None = None) -> Cell:
    return Cell(
        display=display if display is not None else ("" if cached is None else str(cached)),
        type=CellType.FORMULA,
        value=cached,
        formula=formula,
        cached=cached,
    )


def _save_shot(widget, name: str) -> Path:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / name
    assert widget.grab().save(str(path)), f"截图保存失败：{path}"
    return path


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    font_path = TESTS_DIR / "assets" / "NotoSansSC-Regular.otf"
    if font_path.exists():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                app.setFont(QFont(families[0], 10))
    return app


@pytest.fixture(scope="session")
def sample_data():
    left = load_workbook(FIXTURES / "sample_left.xlsx")
    right = load_workbook(FIXTURES / "sample_right.xlsx")
    return left, right, compare_workbooks(left, right)


# ---------------------------------------------------------------------------
# 读取：XLSX
# ---------------------------------------------------------------------------


def test_load_xlsx_preserves_sheets_merges_sizes_styles() -> None:
    workbook = load_workbook(FIXTURES / "sample_left.xlsx")
    assert workbook.fmt == "xlsx"
    assert [sheet.name for sheet in workbook.sheets] == ["基本信息", "参数表", "仅左侧", "合并差异"]
    sheet = workbook.sheet("基本信息")
    assert sheet is not None
    assert (sheet.nrows, sheet.ncols) == (7, 4)
    # 矩形 rows
    assert all(len(row) == sheet.ncols for row in sheet.rows)
    # 合并单元格：锚点写 span，其余写 merged_anchor，Sheet.merged 完整
    assert (0, 0, 1, 4) in sheet.merged
    assert sheet.cell(0, 0).span == (1, 4)
    assert sheet.cell(0, 1).merged_anchor == (0, 0)
    assert sheet.cell(6, 1).span == (1, 2)
    assert sheet.cell(6, 2).merged_anchor == (6, 1)
    # 列宽 / 行高换算为像素
    assert sheet.col_widths == {0: 173, 1: 131, 2: 103, 3: 75}
    assert sheet.row_heights == {0: 43, 1: 32, 6: 35}
    assert sheet.default_col_width == 72.0
    assert sheet.default_row_height == 20.0
    # 基本样式
    title = sheet.cell(0, 0)
    assert title.type == CellType.TEXT
    assert title.display == "设备定值单"
    assert title.style.bold is True
    assert title.style.font_size == 16.0
    assert title.style.font_color == "#C00000"
    assert title.style.fill_color == "#FFF2CC"
    assert title.style.h_align == "center"
    assert title.style.v_align == "center"
    notice = sheet.cell(1, 2)
    assert notice.style.italic is True
    assert notice.style.fill_color == "#FCE4D6"
    assert notice.style.font_color == "#FF0000"
    assert notice.style.h_align == "right"
    assert notice.style.v_align == "bottom"
    # 数值 / 日期展示格式
    assert sheet.cell(4, 1).type == CellType.NUMBER
    assert sheet.cell(4, 1).display == "1.50"
    assert sheet.cell(4, 1).style.number_format == "0.00"
    assert sheet.cell(5, 1).type == CellType.DATE
    assert sheet.cell(5, 1).display == "2024-05-06"
    # 不同表的列宽、合并互不串扰
    params = workbook.sheet("参数表")
    assert params is not None
    assert params.col_widths == {0: 187, 1: 145, 2: 103, 3: 75}
    assert params.cell(5, 1).merged_anchor == (5, 0)


def test_load_xlsx_formula_and_cached_values() -> None:
    workbook = load_workbook(FIXTURES / "formula_left.xlsx")
    sheet = workbook.sheet("公式与缓存值")
    assert sheet is not None
    assert [cell.type for cell in sheet.rows[0]] == [CellType.NUMBER, CellType.FORMULA]
    b1 = sheet.cell(0, 1)
    assert b1.formula == "=A1+A2"
    assert b1.cached == 5
    assert b1.display == "5"
    b2 = sheet.cell(1, 1)
    assert b2.formula == "=A1*A2"
    assert b2.cached == 6
    assert b2.display == "6.00"  # number_format=0.00
    # 未注入缓存值的公式：formula 保留、cached=None、display 回退为公式文本
    b3 = sheet.cell(2, 1)
    assert b3.formula == "=A1-A2"
    assert b3.cached is None
    assert b3.display == "=A1-A2"
    # 字符串缓存值
    b4 = sheet.cell(3, 1)
    assert b4.formula == '=IF(A1>1,"是","否")'
    assert b4.cached == "是"
    assert b4.display == "是"
    assert any("缓存值" in warning for warning in workbook.warnings)

    right = load_workbook(FIXTURES / "formula_right.xlsx")
    right_sheet = right.sheet("公式与缓存值")
    assert right_sheet is not None
    assert right_sheet.cell(4, 1).display == "0.67"
    assert right_sheet.cell(4, 1).cached == 0.67


# ---------------------------------------------------------------------------
# 读取：XLS
# ---------------------------------------------------------------------------


def test_load_xls_basics_and_formula_warning() -> None:
    workbook = load_workbook(FIXTURES / "sample_left.xls")
    assert workbook.fmt == "xls"
    assert [sheet.name for sheet in workbook.sheets] == ["XLS示例", "相同表"]
    assert any("公式" in warning for warning in workbook.warnings)
    sheet = workbook.sheet("XLS示例")
    assert sheet is not None
    assert (sheet.nrows, sheet.ncols) == (3, 6)
    assert sheet.merged == [(1, 4, 1, 2)]
    assert sheet.cell(1, 4).display == "合并值"
    assert sheet.cell(1, 5).merged_anchor == (1, 4)
    assert sheet.col_widths == {0: 145, 1: 89, 2: 75, 3: 117, 4: 131, 5: 131}
    assert sheet.row_heights[0] == 40
    assert sheet.row_heights[1] == 32
    assert sheet.row_heights[2] == 17  # xls 文件中的显式默认行高信息
    # 日期
    date_cell = sheet.cell(1, 3)
    assert date_cell.type == CellType.DATE
    assert date_cell.display == "2024-05-06"
    # 样式：表头粗体/居中方块，notice 斜体红字橙底
    header = sheet.cell(0, 0)
    assert header.style.bold is True
    assert header.style.h_align == "center"
    assert header.style.fill_color is not None
    notice = sheet.cell(1, 1)
    assert notice.style.italic is True
    assert notice.style.font_color == "#FF0000"
    assert notice.style.fill_color is not None
    assert notice.style.h_align == "right"
    assert sheet.cell(2, 2).display == "0.85"
    assert sheet.cell(2, 2).type == CellType.NUMBER


def test_load_errors_are_chinese_and_typed(tmp_path: Path) -> None:
    with pytest.raises(SpreadsheetLoadError) as excinfo:
        load_workbook(tmp_path / "不存在.xlsx")
    assert "不存在" in str(excinfo.value)

    wrong_ext = tmp_path / "data.txt"
    wrong_ext.write_text("x", encoding="utf-8")
    with pytest.raises(SpreadsheetLoadError) as excinfo:
        load_workbook(wrong_ext)
    assert "仅支持" in str(excinfo.value)

    corrupt = tmp_path / "broken.xlsx"
    corrupt.write_bytes(b"this is not an excel file")
    with pytest.raises(SpreadsheetLoadError) as excinfo:
        load_workbook(corrupt)
    assert "无法读取" in str(excinfo.value)

    with pytest.raises(SpreadsheetLoadError) as excinfo:
        load_workbook(tmp_path)
    assert "不是文件" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 比较：真实工作簿
# ---------------------------------------------------------------------------


def test_compare_sample_workbooks(sample_data) -> None:
    _left, _right, comparison = sample_data
    expected_names = ["基本信息", "参数表", "仅左侧", "合并差异", "仅右侧"]
    assert [sheet.name for sheet in comparison.sheets] == expected_names
    statuses = {sheet.name: sheet.status for sheet in comparison.sheets}
    assert statuses["基本信息"] == SheetStatus.DIFFERENT
    assert statuses["参数表"] == SheetStatus.EQUAL  # 顺序不同但内容相同
    assert statuses["仅左侧"] == SheetStatus.ONLY_LEFT
    assert statuses["仅右侧"] == SheetStatus.ONLY_RIGHT
    assert statuses["合并差异"] == SheetStatus.DIFFERENT
    # 合并区域不一致会产生一条橙色复核提示，但不应有“缓存值缺失”类警告
    assert all("缓存值缺失" not in warning for warning in comparison.warnings)
    assert any("复核" in warning for warning in comparison.warnings)

    ids = [diff.id for diff in comparison.diffs]
    assert ids == [
        "cell:基本信息:1:1",
        "cell:基本信息:3:1",
        "sheet:仅左侧",
        "cell:合并差异:0:0",
        "sheet:仅右侧",
    ]
    info = comparison.sheet_by_name("基本信息")
    assert info is not None
    assert info.left_index == 0 and info.right_index == 1
    version_diff = comparison.diffs[0]
    assert version_diff.group == "cell"
    assert version_diff.severity == Severity.CONFIRMED
    assert version_diff.left is not None and version_diff.right is not None
    assert (version_diff.left.row, version_diff.left.col) == (1, 1)
    assert version_diff.left.text == "V1.0.0"
    assert version_diff.right.text == "V1.0.1"
    assert "文本不同" in version_diff.detail
    type_diff = comparison.diffs[1]
    assert (type_diff.left.row, type_diff.left.col) == (3, 1)
    assert type_diff.left.text == "220.00"
    assert type_diff.right.text == "220"
    assert "类型不同" in type_diff.detail

    left_only = comparison.sheet_by_name("仅左侧")
    assert left_only is not None and left_only.left_index == 2 and left_only.right_index is None
    sheet_diff = comparison.diffs[2]
    assert sheet_diff.kind == DiffKind.ONLY_LEFT
    assert sheet_diff.group == "sheet"
    assert sheet_diff.left is not None and sheet_diff.left.kind == "sheet"
    assert sheet_diff.left.text == "仅左侧"
    assert sheet_diff.right is None
    right_only = comparison.diffs[4]
    assert right_only.kind == DiffKind.ONLY_RIGHT
    assert right_only.right is not None and right_only.right.text == "仅右侧"


def test_merge_mismatch_is_single_orange_anchor_diff(sample_data) -> None:
    _left, _right, comparison = sample_data
    merge_sheet = comparison.sheet_by_name("合并差异")
    assert merge_sheet is not None
    assert merge_sheet.status == SheetStatus.DIFFERENT
    assert len(merge_sheet.diffs) == 1  # 锚点一条，不重复内部空格
    diff = merge_sheet.diffs[0]
    assert diff.id == "cell:合并差异:0:0"
    assert diff.severity == Severity.UNCERTAIN
    assert diff.needs_review is True
    assert diff.detail == "合并单元格区域不一致"
    assert diff.left is not None and diff.right is not None
    assert diff.left.text == diff.right.text == "合并区域"
    assert diff.left.extra["span"] == [1, 2]
    assert diff.right.extra["span"] == [1, 3]
    # 合并结构相同的“参数表”不产生合并差异
    params = comparison.sheet_by_name("参数表")
    assert params is not None
    assert params.diffs == []


def test_compare_empty_workbooks() -> None:
    comparison = compare_workbooks(_workbook(), _workbook())
    assert comparison.sheets == []
    assert comparison.diffs == []
    assert comparison.warnings == []
    assert comparison.settings == SpreadsheetOptions().as_dict()


# ---------------------------------------------------------------------------
# 比较：内存工作簿（严格坐标 / 类型 / 空白 / 容差）
# ---------------------------------------------------------------------------


def test_blank_and_type_differences() -> None:
    left = _workbook(_sheet("S", [[None, 1, 1.5, True]]))
    right = _workbook(_sheet("S", [["x", "1", 1.5, False]]))
    comparison = compare_workbooks(left, right)
    assert [diff.id for diff in comparison.diffs] == ["cell:S:0:0", "cell:S:0:1", "cell:S:0:3"]
    blank_diff, type_diff, bool_diff = comparison.diffs
    assert "类型不同" in blank_diff.detail
    assert "空白" in blank_diff.detail
    assert blank_diff.left is not None and blank_diff.left.text == ""
    assert blank_diff.right is not None and blank_diff.right.text == "x"
    assert "数值 ↔ 文本" in type_diff.detail
    assert "布尔值不同" in bool_diff.detail
    # 1.5 与 1.5 数值相同 -> 无差异
    assert all(diff.id != "cell:S:0:2" for diff in comparison.diffs)


def test_added_and_removed_by_bounds() -> None:
    left = _workbook(_sheet("S", [["keep"], ["left-only"]]))
    right = _workbook(_sheet("S", [["keep"]]))
    comparison = compare_workbooks(left, right)
    assert len(comparison.diffs) == 1
    removed = comparison.diffs[0]
    assert removed.kind == DiffKind.REMOVED
    assert removed.left is not None and removed.left.text == "left-only"
    assert removed.right is None
    assert (removed.left.row, removed.left.col) == (1, 0)

    reverse = compare_workbooks(right, left)
    assert len(reverse.diffs) == 1
    added = reverse.diffs[0]
    assert added.kind == DiffKind.ADDED
    assert added.left is None
    assert added.right is not None and added.right.text == "left-only"


def test_numeric_tolerance_and_decimal_equality() -> None:
    equal = compare_workbooks(
        _workbook(_sheet("S", [[1.50, 1]])),
        _workbook(_sheet("S", [[1.5, 1.0]])),
    )
    assert equal.diffs == []

    strict = compare_workbooks(
        _workbook(_sheet("S", [[0.6666666666]])),
        _workbook(_sheet("S", [[0.67]])),
    )
    assert len(strict.diffs) == 1
    assert "数值不同" in strict.diffs[0].detail

    same_display = compare_workbooks(
        _workbook(_sheet("S", [[Cell(display="0.67", type=CellType.NUMBER, value=0.6666666666)]])),
        _workbook(_sheet("S", [[Cell(display="0.67", type=CellType.NUMBER, value=0.67)]])),
    )
    assert len(same_display.diffs) == 1
    assert "原始值" in same_display.diffs[0].detail

    tolerant = compare_workbooks(
        _workbook(_sheet("S", [[0.6666666666]])),
        _workbook(_sheet("S", [[0.67]])),
        SpreadsheetOptions(numeric_tolerance=0.01),
    )
    assert tolerant.diffs == []

    # 相对差：100 与 100.5 在 1% 容差内视为相同
    relative = compare_workbooks(
        _workbook(_sheet("S", [[100.0]])),
        _workbook(_sheet("S", [[100.5]])),
        SpreadsheetOptions(numeric_tolerance=0.01),
    )
    assert relative.diffs == []

    outside = compare_workbooks(
        _workbook(_sheet("S", [[100.0]])),
        _workbook(_sheet("S", [[102.0]])),
        SpreadsheetOptions(numeric_tolerance=0.01),
    )
    assert len(outside.diffs) == 1
    assert "超出容差" in outside.diffs[0].detail


def test_added_removed_columns_are_strict_coordinates() -> None:
    left = _workbook(_sheet("S", [["A", "B"]]))
    right = _workbook(_sheet("S", [["A"]]))
    comparison = compare_workbooks(left, right)
    assert [diff.id for diff in comparison.diffs] == ["cell:S:0:1"]
    assert comparison.diffs[0].kind == DiffKind.REMOVED
    assert comparison.diffs[0].right is None


# ---------------------------------------------------------------------------
# 公式模式
# ---------------------------------------------------------------------------


def test_formula_modes_on_fixtures() -> None:
    left = load_workbook(FIXTURES / "formula_left.xlsx")
    right = load_workbook(FIXTURES / "formula_right.xlsx")

    formula_comparison = compare_workbooks(left, right)
    assert [diff.id for diff in formula_comparison.diffs] == ["cell:公式与缓存值:1:1"]
    formula_diff = formula_comparison.diffs[0]
    assert "公式不同" in formula_diff.detail
    assert formula_diff.severity == Severity.CONFIRMED

    value_comparison = compare_workbooks(left, right, SpreadsheetOptions(formula_mode="value"))
    assert [diff.id for diff in value_comparison.diffs] == [
        "cell:公式与缓存值:2:1",
        "cell:公式与缓存值:4:1",
    ]
    missing_cache = value_comparison.diffs[0]
    assert missing_cache.severity == Severity.UNCERTAIN
    assert missing_cache.needs_review is True
    assert "缓存值缺失" in missing_cache.detail
    assert missing_cache.left is not None and missing_cache.left.text == "=A1-A2"
    assert missing_cache.right is not None and missing_cache.right.text == "-1"
    assert value_comparison.diffs[1].severity == Severity.CONFIRMED
    assert any("缓存值" in warning for warning in value_comparison.warnings)

    tolerant = compare_workbooks(
        left,
        right,
        SpreadsheetOptions(formula_mode="value", numeric_tolerance=0.01),
    )
    assert [diff.id for diff in tolerant.diffs] == ["cell:公式与缓存值:2:1"]


def test_formula_versus_literal_value() -> None:
    left = _workbook(_sheet("S", [[_formula("=1+1", 2, "2")]]))
    right = _workbook(_sheet("S", [[2]]))
    formula_mode = compare_workbooks(left, right)
    assert len(formula_mode.diffs) == 1
    assert "一侧为公式" in formula_mode.diffs[0].detail
    value_mode = compare_workbooks(left, right, SpreadsheetOptions(formula_mode="value"))
    assert value_mode.diffs == []


# ---------------------------------------------------------------------------
# 智能行对齐（实验性，不改变严格模式）
# ---------------------------------------------------------------------------


def test_smart_rows_experimental_does_not_change_strict_default() -> None:
    left = _workbook(_sheet("S", [["A"], ["B"], ["C"]]))
    right = _workbook(_sheet("S", [["X"], ["A"], ["B"], ["C"]]))

    strict = compare_workbooks(left, right)
    assert len(strict.diffs) == 4
    assert strict.warnings == []
    for diff in strict.diffs:
        assert diff.severity == Severity.CONFIRMED

    smart = compare_workbooks(left, right, SpreadsheetOptions(smart_rows=True))
    assert len(smart.diffs) == 1
    added = smart.diffs[0]
    assert added.kind == DiffKind.ADDED
    assert added.left is None
    assert added.right is not None and added.right.row == 0 and added.right.text == "X"
    assert any("智能行对齐" in warning for warning in smart.warnings)

    # 合并单元格时自动回退严格比较
    left_merge = Sheet(
        name="M",
        rows=[[_cell("A"), Cell()]],
        nrows=1,
        ncols=2,
        merged=[(0, 0, 1, 2)],
    )
    left_merge.rows[0][0].span = (1, 2)
    left_merge.rows[0][1].merged_anchor = (0, 0)
    fallback = compare_workbooks(_workbook(left_merge), _workbook(left_merge), SpreadsheetOptions(smart_rows=True))
    assert any("回退" in warning for warning in fallback.warnings)


# ---------------------------------------------------------------------------
# fixture 生成脚本
# ---------------------------------------------------------------------------


def test_fixture_generator_is_reproducible(tmp_path: Path) -> None:
    generated = generate_all(tmp_path)
    names = {path.name for path in generated}
    assert {
        "sample_left.xlsx",
        "sample_right.xlsx",
        "formula_left.xlsx",
        "formula_right.xlsx",
        "sample_left.xls",
        "sample_right.xls",
        "sample_left.csv",
        "sample_right.csv",
    } <= names
    left = load_workbook(tmp_path / "sample_left.xlsx")
    right = load_workbook(tmp_path / "sample_right.xlsx")
    comparison = compare_workbooks(left, right)
    assert [diff.id for diff in comparison.diffs] == [
        "cell:基本信息:1:1",
        "cell:基本信息:3:1",
        "sheet:仅左侧",
        "cell:合并差异:0:0",
        "sheet:仅右侧",
    ]


# ---------------------------------------------------------------------------
# GUI：GridView / SpreadsheetSide
# ---------------------------------------------------------------------------


def _pixel(image, rect, dx: float, dy: float) -> str:
    point = rect.topLeft()
    return image.pixelColor(int(point.x() + dx), int(point.y() + dy)).name()


@pytest.mark.gui
def test_spreadsheet_side_overview_screenshot(qapp, sample_data) -> None:
    left, _right, comparison = sample_data
    side = SpreadsheetSide()
    side.resize(900, 480)
    side.load(left, comparison, "left")
    side.show()
    qapp.processEvents()
    side.show_sheet("基本信息")
    qapp.processEvents()
    grid = side.grid()
    image = side.grab().toImage()

    # 差异红底 + 红色左边框
    version_rect = grid.cell_rect(1, 1)
    assert version_rect.width() > 0
    assert _pixel(image, version_rect, 4, 4) == "#ffd7d5"
    assert _pixel(image, version_rect, 1, version_rect.height() / 2) == "#c62828"
    type_rect = grid.cell_rect(3, 1)
    assert _pixel(image, type_rect, 4, 4) == "#ffd7d5"

    # 合并单元格：跨 4 列、标题底色、内部没有网格线
    merged_rect = grid.cell_rect(0, 0)
    single_col = grid._col_offsets[1] - grid._col_offsets[0]  # noqa: SLF001 - 测试内部几何
    assert merged_rect.width() == pytest.approx(173 + 131 + 103 + 75)
    assert _pixel(image, merged_rect, merged_rect.width() * 0.75, merged_rect.height() - 5) == "#fff2cc"
    internal_x = grid._col_offsets[1]  # noqa: SLF001
    assert _pixel(image, merged_rect, internal_x + 2, merged_rect.height() - 5) == "#fff2cc"

    # 固定表头：左上角/列标区域为表头底色
    assert image.pixelColor(10, 10).name() in ("#f6f8fa", "#fafafa")
    assert image.pixelColor(60, 10).name() in ("#f6f8fa", "#fafafa")

    # 行号显示原始行号（第 7 行标签文本难以直接断言，检查行头区域宽度与内容行高）
    assert grid.cell_rect(6, 0).height() == pytest.approx(35.0)

    # Sheet 标签颜色
    assert side.tab_color("基本信息").name() == "#c62828"
    assert side.tab_color("参数表").name() in ("#57606a", "#616161")
    assert side.tab_color("仅左侧").name() == "#c62828"
    assert side.tab_color("合并差异").name() == "#e65100"
    assert side.tab_color("仅右侧").name() == "#c62828"
    assert side.tab_bar().selected() == "基本信息"

    path = _save_shot(side, "spreadsheet_side_overview.png")
    assert path.stat().st_size > 1000
    side.close()


@pytest.mark.gui
def test_grid_view_only_diffs_and_scroll(qapp) -> None:
    rows_left = [[f"R{row}C{col}" for col in range(4)] for row in range(120)]
    rows_right = [list(row) for row in rows_left]
    for row in range(5, 120, 10):
        rows_right[row][0] = f"CHANGED{row}"
    left = _workbook(_sheet("大表", rows_left))
    right = _workbook(_sheet("大表", rows_right))
    comparison = compare_workbooks(left, right)
    sheet_comparison = comparison.sheets[0]
    assert len(sheet_comparison.diffs) == 12

    grid = GridView()
    grid.resize(800, 300)
    grid.set_sheet(sheet_comparison.left, sheet_comparison.diffs, "left")
    grid.set_only_diffs(True)
    grid.show()
    qapp.processEvents()
    # 12 个差异行 + 前后各 1 行上下文 = 36 行
    assert grid.visible_row_count() == 36
    assert grid.current_cell() is None

    grid.scroll_to_cell(115, 1)
    qapp.processEvents()
    assert grid.current_cell() == (115, 1)
    assert grid.verticalScrollBar().value() > 0

    image = grid.grab().toImage()
    target_rect = grid.cell_rect(115, 0)
    assert target_rect.width() > 0
    assert _pixel(image, target_rect, 4, 4) == "#ffd7d5"
    context_rect = grid.cell_rect(116, 0)
    assert _pixel(image, context_rect, 4, 4) in ("#ffffff", "#f6f8fa")

    path = _save_shot(grid, "grid_view_only_diffs.png")
    assert path.stat().st_size > 1000
    grid.close()


@pytest.mark.gui
def test_only_diffs_persists_across_sheet_switch(qapp, sample_data) -> None:
    left, _right, comparison = sample_data
    side = SpreadsheetSide()
    side.resize(900, 480)
    side.load(left, comparison, "left")
    side.show()
    qapp.processEvents()
    side.show_sheet("基本信息")
    side.set_only_diffs(True)
    qapp.processEvents()
    grid = side.grid()
    assert grid.only_diffs() is True
    assert grid.visible_row_count() == 5

    side.show_sheet("参数表")  # 无差异 Sheet
    qapp.processEvents()
    assert grid.only_diffs() is True  # 切 Sheet 后过滤模式保留
    assert grid.visible_row_count() == 0

    side.show_sheet("基本信息")
    qapp.processEvents()
    assert grid.only_diffs() is True
    assert grid.visible_row_count() == 5
    side.set_only_diffs(False)
    side.close()


@pytest.mark.gui
def test_csv_multiline_field_rendered_fully(qapp) -> None:
    from engineerdiff.spreadsheet import compare_csv_files, csv_to_workbook, parse_csv

    left_path = FIXTURES / "sample_left.csv"
    comparison, delimiter, _warnings = compare_csv_files(left_path, FIXTURES / "sample_right.csv")
    assert delimiter == ","
    left_sheet, _delimiter, parse_warnings = parse_csv(left_path)
    left_workbook = csv_to_workbook(left_sheet, left_path, parse_warnings)

    side = SpreadsheetSide()
    side.resize(900, 360)
    side.load(left_workbook, comparison, "left")
    side.show()
    qapp.processEvents()
    side.show_sheet("CSV")
    qapp.processEvents()
    grid = side.grid()
    cell = grid.sheet().cell(1, 2)
    assert cell.style.wrap is True
    assert "\n" in cell.display
    assert grid.sheet().row_heights[1] >= 40
    assert grid.sheet().col_widths[2] >= 180

    rect = grid.cell_rect(1, 2)
    assert rect.height() >= 40
    image = side.grab().toImage()
    # 换行后的第二行文字必须落在单元格内（下半区存在深色文字像素）
    dark_pixels = 0
    for y in range(int(rect.y() + rect.height() * 0.55), int(rect.bottom() - 1)):
        for x in range(int(rect.x() + 3), int(rect.right() - 3)):
            if image.pixelColor(x, y).lightness() < 120:
                dark_pixels += 1
    assert dark_pixels > 10
    assert _save_shot(side, "csv_multiline_wrap.png").stat().st_size > 1000
    side.close()


@pytest.mark.gui
def test_grid_view_merge_and_zoom(qapp, sample_data) -> None:
    left, _right, comparison = sample_data
    side = SpreadsheetSide()
    side.resize(900, 480)
    side.load(left, comparison, "left")
    side.show()
    qapp.processEvents()
    side.show_sheet("合并差异")
    qapp.processEvents()
    grid = side.grid()

    base_rect = grid.cell_rect(0, 0)  # 此时 zoom=1
    assert base_rect.width() == pytest.approx(145 + 131)
    grid.set_zoom(2.0)
    qapp.processEvents()
    merged_rect = grid.cell_rect(0, 0)
    assert grid.zoom() == 2.0
    assert merged_rect.width() == pytest.approx(base_rect.width() * 2)
    assert merged_rect.height() == pytest.approx(base_rect.height() * 2)
    image = side.grab().toImage()
    # 合并区域不一致 -> 橙色高亮覆盖整个合并区域（不重复内部空格）
    assert _pixel(image, merged_rect, merged_rect.width() * 0.7, 4) == "#ffe2b8"
    assert len(comparison.sheet_by_name("合并差异").diffs) == 1
    assert _save_shot(side, "grid_view_merge_zoom.png").stat().st_size > 1000

    grid.set_zoom(10.0)
    assert grid.zoom() == 2.5
    grid.set_zoom(0.1)
    assert grid.zoom() == 0.5
    side.close()


@pytest.mark.gui
def test_spreadsheet_side_placeholder_reveal_and_signals(qapp, sample_data) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    left, right, comparison = sample_data
    side = SpreadsheetSide()
    side.resize(900, 480)
    side.load(left, comparison, "left")
    side.show()
    qapp.processEvents()

    # 标签列表按比较结果包含“仅一侧存在”的 Sheet（两侧一致）
    assert side.sheet_names() == ["基本信息", "参数表", "仅左侧", "合并差异", "仅右侧"]
    right_side = SpreadsheetSide()
    right_side.resize(900, 480)
    right_side.load(right, comparison, "right")
    right_side.show()
    qapp.processEvents()
    assert right_side.sheet_names() == ["基本信息", "参数表", "仅左侧", "合并差异", "仅右侧"]
    right_side.show_sheet("仅左侧")
    qapp.processEvents()
    assert right_side.is_placeholder_visible() is True
    assert right_side.placeholder_text() == "无对应工作表"
    right_side.close()

    # 本侧没有“仅右侧”工作表 -> 居中占位，保留标签选中态
    side.show_sheet("仅右侧")
    qapp.processEvents()
    assert side.is_placeholder_visible() is True
    assert side.placeholder_text() == "无对应工作表"
    assert side.current_sheet() == "仅右侧"
    assert side.tab_bar().selected() == "仅右侧"
    assert side.tab_color("仅右侧").name() == "#c62828"
    _save_shot(side, "spreadsheet_side_placeholder.png")

    # 点击标签 -> 发 sheet_selected 并切回真实 Sheet
    received: list[str] = []
    side.sheet_selected.connect(received.append)
    target_rect = side.tab_bar().tab_rect("仅左侧")
    assert target_rect.isValid() and target_rect.width() > 0
    QTest.mouseClick(side.tab_bar(), Qt.LeftButton, pos=target_rect.center())
    qapp.processEvents()
    assert received == ["仅左侧"]
    assert side.current_sheet() == "仅左侧"
    assert side.is_placeholder_visible() is False
    assert side.grid().sheet() is not None and side.grid().sheet().name == "仅左侧"

    # reveal 单元格差异：切 Sheet + scroll_to_cell
    cell_diff = comparison.sheet_by_name("基本信息").diffs[0]
    side.reveal(cell_diff)
    qapp.processEvents()
    assert side.current_sheet() == "基本信息"
    assert side.grid().current_cell() == (1, 1)

    # reveal 仅对侧存在的工作表：只切标签并显示占位
    right_only = comparison.sheet_by_name("仅右侧").diffs[0]
    side.reveal(right_only)
    qapp.processEvents()
    assert side.current_sheet() == "仅右侧"
    assert side.is_placeholder_visible() is True

    # 本侧确实没有的 Sheet 名称
    side.show_sheet("不存在的表")
    qapp.processEvents()
    assert side.placeholder_text() == "无对应工作表"
    assert side.current_sheet() == "不存在的表"
    assert side.sheet_names() == ["基本信息", "参数表", "仅左侧", "合并差异", "仅右侧"]
    side.close()


@pytest.mark.gui
def test_spreadsheet_side_empty_workbook(qapp) -> None:
    empty = Workbook(path="empty.xlsx", sheets=[], fmt="xlsx")
    side = SpreadsheetSide()
    side.resize(600, 320)
    side.load(empty, None, "left")
    side.show()
    qapp.processEvents()
    assert side.is_placeholder_visible() is True
    assert side.placeholder_text() == "工作簿中没有工作表"
    assert side.current_sheet() is None
    side.close()


@pytest.mark.gui
def test_scroll_changed_signal_and_sync_ratio(qapp) -> None:
    rows_left = [[f"R{row}C{col}" for col in range(4)] for row in range(200)]
    rows_right = [list(row) for row in rows_left]
    rows_right[150][1] = "CHANGED"
    left = _workbook(_sheet("大表", rows_left))
    right = _workbook(_sheet("大表", rows_right))
    comparison = compare_workbooks(left, right)
    side = SpreadsheetSide()
    side.resize(800, 320)
    side.load(left, comparison, "left")
    side.show()
    qapp.processEvents()
    side.show_sheet("大表")
    qapp.processEvents()

    captured: list[tuple[float, float]] = []
    side.scroll_changed.connect(lambda vertical, horizontal: captured.append((vertical, horizontal)))
    side.grid().verticalScrollBar().setValue(200)
    qapp.processEvents()
    assert captured
    assert captured[-1][0] > 0

    # set_scroll_ratio 由 MainWindow 调用，不应反向发信号（防递归）
    captured.clear()
    side.set_scroll_ratio(0.5, 0.0)
    qapp.processEvents()
    assert captured == []
    vbar = side.grid().verticalScrollBar()
    assert vbar.value() == pytest.approx(vbar.maximum() / 2, abs=2)
    side.close()


def test_screenshots_written() -> None:
    expected = [
        "spreadsheet_side_overview.png",
        "spreadsheet_side_placeholder.png",
        "grid_view_only_diffs.png",
        "grid_view_merge_zoom.png",
        "csv_multiline_wrap.png",
    ]
    for name in expected:
        path = SHOT_DIR / name
        assert path.exists(), f"缺少截图：{path}"
        assert path.stat().st_size > 1000


@pytest.mark.gui
def test_many_sheet_tabs_are_scrollable(qapp) -> None:
    """大量 Sheet 时标签栏可滚动；选中后面的表会自动滚入可视区。"""
    names = [f"Sheet{i:02d}" for i in range(30)]
    workbook = _workbook(*[_sheet(name, [["x"]]) for name in names])
    side = SpreadsheetSide()
    side.resize(360, 240)
    side.load(workbook, None, "left")
    side.show()
    qapp.processEvents()

    bar = side.tab_bar()
    assert bar.has_overflow()
    assert side._tab_right.isVisible() is True
    assert side._tab_left.isVisible() is True

    side.show_sheet(names[-1])
    qapp.processEvents()
    last_rect = bar.tab_rect(names[-1])
    assert last_rect.left() >= 0
    assert last_rect.right() <= bar.width()
    assert bar._offset > 0

    before = bar._offset
    side._tab_left.click()
    qapp.processEvents()
    assert bar._offset <= before

    side.close()
