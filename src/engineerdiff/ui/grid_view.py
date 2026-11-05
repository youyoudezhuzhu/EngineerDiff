"""自绘虚拟化表格视图（QPainter + QAbstractScrollArea）。

契约见 docs/ui-contracts.md 2.4：固定列标/行号表头、合并单元格跨格绘制、
列宽行高与缩放相乘、只绘制视口内单元格、差异红/橙高亮、only_diffs 行过滤、
scroll_to_cell 等。

实现说明：

* 所有单元格都由 ``paintEvent`` 自绘，不使用 QTableWidget，因此可以只绘制
  视口内可见行列（万行级工作表不会一次性创建控件）。
* 差异从传入的 ``list[DiffItem]`` 按本侧 ``DiffLocation`` 构建 ``(row,col)``
  映射；对侧不存在的差异不会在本侧高亮。
* 合并单元格以锚点为一个整体绘制；内部单元格不画网格线。
* ``zoom`` 只缩放内容区（列宽/行高/字体），表头保持固定尺寸。
"""

from __future__ import annotations

import math
from bisect import bisect_right
from typing import Iterable

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QWidget

from ..models import Cell, CellType, DiffItem, DiffLocation, Severity, Sheet
from . import theme

HEADER_WIDTH = 52.0
HEADER_HEIGHT = 24.0

COLOR_BACKGROUND = QColor("#FFFFFF")
COLOR_HEADER_BG = QColor("#FAFAFA")
COLOR_HEADER_TEXT = QColor("#616161")
COLOR_GRID = QColor("#E0E0E0")
COLOR_TEXT = QColor("#1B1B1B")
COLOR_DIFF_RED_BG = QColor("#FFD7D5")
COLOR_DIFF_RED_BORDER = QColor("#C62828")
COLOR_DIFF_ORANGE_BG = QColor("#FFE2B8")
COLOR_DIFF_ORANGE_BORDER = QColor("#E65100")
COLOR_SELECTED = QColor("#0067C0")
COLOR_HEADER_HIGHLIGHT = QColor("#E5F1FB")


def refresh_palette() -> None:
    """主题切换时更新模块级绘制颜色；所有 GridView 下次重绘即生效。"""
    global COLOR_BACKGROUND, COLOR_HEADER_BG, COLOR_HEADER_TEXT, COLOR_GRID, COLOR_TEXT
    global COLOR_DIFF_RED_BG, COLOR_DIFF_RED_BORDER, COLOR_DIFF_ORANGE_BG
    global COLOR_DIFF_ORANGE_BORDER, COLOR_SELECTED, COLOR_HEADER_HIGHLIGHT
    p = theme.palette()
    COLOR_BACKGROUND = QColor(p.card_bg)
    COLOR_HEADER_BG = QColor(p.secondary_surface)
    COLOR_HEADER_TEXT = QColor(p.text_secondary)
    COLOR_GRID = QColor(p.divider)
    COLOR_TEXT = QColor(p.text)
    COLOR_DIFF_RED_BG = QColor(p.diff_red_bg)
    COLOR_DIFF_RED_BORDER = QColor(p.diff_red)
    COLOR_DIFF_ORANGE_BG = QColor(p.warning_bg)
    COLOR_DIFF_ORANGE_BORDER = QColor(p.warning)
    COLOR_SELECTED = QColor(p.accent)
    COLOR_HEADER_HIGHLIGHT = QColor(p.accent_soft)

MIN_ZOOM = 0.5
MAX_ZOOM = 2.5


class GridView(QAbstractScrollArea):
    """虚拟化表格视图。"""

    cell_clicked = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.viewport().setAutoFillBackground(False)

        self._sheet: Sheet | None = None
        self._diffs: list[DiffItem] = []
        self._side = "left"
        self._uncertain = False
        self._diff_map: dict[tuple[int, int], DiffItem] = {}

        self._zoom = 1.0
        self._only_diffs = False
        self._show_grid = True
        self._selected: tuple[int, int] | None = None

        self._col_offsets: list[float] = [0.0]
        self._row_offsets: list[float] = [0.0]
        self._content_width = 0.0
        self._content_height = 0.0
        self._visible_rows: list[int] = []
        self._row_display: dict[int, int] = {}

        self._anchors: list[tuple[int, int, int, int]] = []
        self._covered: set[tuple[int, int]] = set()

    # ------------------------------------------------------------------
    # 契约接口
    # ------------------------------------------------------------------

    def set_sheet(
        self,
        sheet: Sheet | None,
        diffs: list[DiffItem] | None,
        side: str,
        uncertain: bool = False,
    ) -> None:
        same_sheet = (
            sheet is not None
            and self._sheet is not None
            and sheet.name == self._sheet.name
            and side == self._side
        )
        self._sheet = sheet
        self._diffs = list(diffs or [])
        self._side = "right" if side == "right" else "left"
        self._uncertain = bool(uncertain)
        self._diff_map = self._build_diff_map(self._diffs, self._side)
        self._rebuild_anchors()
        self._rebuild_geometry()
        self._selected = None
        if not same_sheet:
            self.horizontalScrollBar().setValue(0)
            self.verticalScrollBar().setValue(0)
        self.viewport().update()

    def set_zoom(self, zoom: float) -> None:
        try:
            value = float(zoom)
        except (TypeError, ValueError):
            return
        value = max(MIN_ZOOM, min(MAX_ZOOM, value))
        if abs(value - self._zoom) < 1e-6:
            return
        hbar = self.horizontalScrollBar()
        vbar = self.verticalScrollBar()
        h_ratio = hbar.value() / hbar.maximum() if hbar.maximum() else 0.0
        v_ratio = vbar.value() / vbar.maximum() if vbar.maximum() else 0.0
        self._zoom = value
        self._rebuild_geometry()
        hbar.setValue(round(h_ratio * hbar.maximum()))
        vbar.setValue(round(v_ratio * vbar.maximum()))
        self.viewport().update()

    def zoom(self) -> float:
        return self._zoom

    def set_only_diffs(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._only_diffs:
            return
        self._only_diffs = enabled
        self._rebuild_geometry()
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self.viewport().update()

    def only_diffs(self) -> bool:
        return self._only_diffs

    def scroll_to_cell(self, row: int, col: int) -> None:
        sheet = self._sheet
        if sheet is None or sheet.ncols <= 0:
            return
        row = max(0, min(int(row), sheet.nrows - 1))
        col = max(0, min(int(col), sheet.ncols - 1))
        display_index = self._row_display.get(row)
        if display_index is None:
            # only_diffs 下目标行可能被过滤：滚动到最近的可见行。
            if not self._visible_rows:
                return
            display_index = min(range(len(self._visible_rows)), key=lambda i: abs(self._visible_rows[i] - row))
        x = HEADER_WIDTH + self._col_offsets[col]
        y = HEADER_HEIGHT + self._row_offsets[display_index]
        vp = self.viewport()
        hbar = self.horizontalScrollBar()
        vbar = self.verticalScrollBar()
        hbar.setValue(int(max(0, min(hbar.maximum(), x - vp.width() / 2))))
        vbar.setValue(int(max(0, min(vbar.maximum(), y - vp.height() / 2))))
        self._selected = (row, col)
        self.viewport().update()

    def current_cell(self) -> tuple[int, int] | None:
        return self._selected

    def set_show_grid(self, show: bool) -> None:
        self._show_grid = bool(show)
        self.viewport().update()

    # ------------------------------------------------------------------
    # 供集成 / 测试使用的只读辅助接口
    # ------------------------------------------------------------------

    def sheet(self) -> Sheet | None:
        return self._sheet

    def side(self) -> str:
        return self._side

    def visible_row_count(self) -> int:
        return len(self._visible_rows)

    def cell_rect(self, row: int, col: int) -> QRectF:
        """返回单元格在 viewport 坐标系中的矩形；不可见时返回空矩形。"""
        sheet = self._sheet
        if sheet is None or sheet.nrows <= 0 or sheet.ncols <= 0:
            return QRectF()
        if not (0 <= row < sheet.nrows and 0 <= col < sheet.ncols):
            return QRectF()
        display_index = self._row_display.get(row)
        if display_index is None:
            return QRectF()
        cell = sheet.rows[row][col]
        if cell.merged_anchor is not None:
            return QRectF()
        row_span, col_span = cell.span or (1, 1)
        row_span = max(1, min(row_span, sheet.nrows - row))
        col_span = max(1, min(col_span, sheet.ncols - col))
        x = HEADER_WIDTH + self._col_offsets[col] - self.horizontalScrollBar().value()
        y = HEADER_HEIGHT + self._row_offsets[display_index] - self.verticalScrollBar().value()
        width = self._col_offsets[min(sheet.ncols, col + col_span)] - self._col_offsets[col]
        height = self._row_height_sum(row, row_span)
        return QRectF(x, y, width, height)

    def content_size(self) -> QSize:
        return QSize(int(math.ceil(self._content_width)), int(math.ceil(self._content_height)))

    # ------------------------------------------------------------------
    # 内部：差异 / 合并结构
    # ------------------------------------------------------------------

    @staticmethod
    def _build_diff_map(diffs: Iterable[DiffItem], side: str) -> dict[tuple[int, int], DiffItem]:
        mapping: dict[tuple[int, int], DiffItem] = {}
        for diff in diffs:
            location: DiffLocation | None = diff.left if side == "left" else diff.right
            if location is None or location.kind != "cell":
                continue
            if location.row is None or location.col is None:
                continue
            mapping[(location.row, location.col)] = diff
        return mapping

    def _rebuild_anchors(self) -> None:
        self._anchors = []
        self._covered = set()
        sheet = self._sheet
        if sheet is None or sheet.nrows <= 0 or sheet.ncols <= 0:
            return
        anchors: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        for row, col, row_span, col_span in sheet.merged:
            if row_span <= 0 or col_span <= 0 or row >= sheet.nrows or col >= sheet.ncols:
                continue
            row_span = min(row_span, sheet.nrows - row)
            col_span = min(col_span, sheet.ncols - col)
            anchors[(row, col)] = (row, col, row_span, col_span)
        if not anchors:
            for row in range(sheet.nrows):
                for col in range(sheet.ncols):
                    span = sheet.rows[row][col].span
                    if not span:
                        continue
                    row_span = min(span[0], sheet.nrows - row)
                    col_span = min(span[1], sheet.ncols - col)
                    anchors[(row, col)] = (row, col, row_span, col_span)
        for row, col, row_span, col_span in anchors.values():
            self._anchors.append((row, col, row_span, col_span))
            for rr in range(row, row + row_span):
                for cc in range(col, col + col_span):
                    if (rr, cc) != (row, col):
                        self._covered.add((rr, cc))
        self._anchors.sort(key=lambda item: (item[0], item[1]))

    # ------------------------------------------------------------------
    # 内部：几何与滚动
    # ------------------------------------------------------------------

    def _row_height(self, row: int) -> float:
        sheet = self._sheet
        if sheet is None:
            return 0.0
        return float(sheet.row_heights.get(row, sheet.default_row_height)) * self._zoom

    def _row_height_sum(self, row: int, span: int) -> float:
        return sum(self._row_height(row + offset) for offset in range(span))

    def _col_width(self, col: int) -> float:
        sheet = self._sheet
        if sheet is None:
            return 0.0
        return float(sheet.col_widths.get(col, sheet.default_col_width)) * self._zoom

    def _rebuild_geometry(self) -> None:
        sheet = self._sheet
        if sheet is None or sheet.nrows <= 0 or sheet.ncols <= 0:
            self._col_offsets = [0.0]
            self._row_offsets = [0.0]
            self._content_width = 0.0
            self._content_height = 0.0
            self._visible_rows = []
            self._row_display = {}
            self._update_scrollbars()
            return

        width_scale = self._zoom
        offsets = [0.0] * (sheet.ncols + 1)
        for col in range(sheet.ncols):
            offsets[col + 1] = offsets[col] + float(
                sheet.col_widths.get(col, sheet.default_col_width)
            ) * width_scale
        self._col_offsets = offsets
        self._content_width = offsets[-1]

        if self._only_diffs:
            rows_with_diff: set[int] = set()
            for diff in self._diffs:
                for location in (diff.left, diff.right):
                    if location is not None and location.kind == "cell" and location.row is not None:
                        rows_with_diff.add(int(location.row))
            visible: set[int] = set()
            for row in rows_with_diff:
                for candidate in (row - 1, row, row + 1):
                    if 0 <= candidate < sheet.nrows:
                        visible.add(candidate)
            self._visible_rows = sorted(visible)
        else:
            self._visible_rows = list(range(sheet.nrows))
        self._row_display = {row: index for index, row in enumerate(self._visible_rows)}

        row_offsets = [0.0] * (len(self._visible_rows) + 1)
        for index, row in enumerate(self._visible_rows):
            row_offsets[index + 1] = row_offsets[index] + self._row_height(row)
        self._row_offsets = row_offsets
        self._content_height = row_offsets[-1]
        self._update_scrollbars()

    def _update_scrollbars(self) -> None:
        viewport = self.viewport()
        total_width = HEADER_WIDTH + self._content_width
        total_height = HEADER_HEIGHT + self._content_height
        hbar = self.horizontalScrollBar()
        vbar = self.verticalScrollBar()
        hbar.setRange(0, max(0, int(math.ceil(total_width - viewport.width()))))
        hbar.setPageStep(max(1, viewport.width()))
        hbar.setSingleStep(max(1, int(round(20 * self._zoom))))
        vbar.setRange(0, max(0, int(math.ceil(total_height - viewport.height()))))
        vbar.setPageStep(max(1, viewport.height()))
        vbar.setSingleStep(max(1, int(round(20 * self._zoom))))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._update_scrollbars()

    def _handle_horizontal_wheel(self, event) -> bool:
        hbar = self.horizontalScrollBar()
        vbar = self.verticalScrollBar()
        if hbar.maximum() <= 0:
            return False
        delta_y = event.angleDelta().y()
        delta_x = event.angleDelta().x()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        fallback = vbar.maximum() <= 0
        if not (shift or fallback or delta_x):
            return False
        delta = delta_y or delta_x
        if delta:
            hbar.setValue(hbar.value() - delta)
        event.accept()
        return True

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._handle_horizontal_wheel(event):
            super().wheelEvent(event)

    def viewportEvent(self, event) -> bool:  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Wheel and self._handle_horizontal_wheel(event):
            return True
        return super().viewportEvent(event)

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802 - Qt API
        super().scrollContentsBy(dx, dy)
        self.viewport().update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(720, 420)

    # ------------------------------------------------------------------
    # 鼠标 / 键盘
    # ------------------------------------------------------------------

    def _cell_at_viewport(self, position) -> tuple[int, int] | None:
        sheet = self._sheet
        if sheet is None or not self._visible_rows:
            return None
        x = position.x() - HEADER_WIDTH + self.horizontalScrollBar().value()
        y = position.y() - HEADER_HEIGHT + self.verticalScrollBar().value()
        if x < 0 or y < 0:
            return None
        col = bisect_right(self._col_offsets, x) - 1
        display_index = bisect_right(self._row_offsets, y) - 1
        if col < 0 or col >= sheet.ncols or display_index < 0 or display_index >= len(self._visible_rows):
            return None
        return self._visible_rows[display_index], col

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton:
            cell = self._cell_at_viewport(event.position())
            if cell is not None:
                self._selected = cell
                self.viewport().update()
                self.cell_clicked.emit(cell[0], cell[1])
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        sheet = self._sheet
        if sheet is None or sheet.nrows <= 0 or sheet.ncols <= 0 or not self._visible_rows:
            super().keyPressEvent(event)
            return
        row, col = self._selected or (self._visible_rows[0], 0)
        display_index = self._row_display.get(row, 0)
        key = event.key()
        if key == Qt.Key_Up:
            display_index = max(0, display_index - 1)
            row = self._visible_rows[display_index]
        elif key == Qt.Key_Down:
            display_index = min(len(self._visible_rows) - 1, display_index + 1)
            row = self._visible_rows[display_index]
        elif key == Qt.Key_Left:
            col = max(0, col - 1)
        elif key == Qt.Key_Right:
            col = min(sheet.ncols - 1, col + 1)
        elif key == Qt.Key_PageUp:
            display_index = max(0, display_index - 20)
            row = self._visible_rows[display_index]
        elif key == Qt.Key_PageDown:
            display_index = min(len(self._visible_rows) - 1, display_index + 20)
            row = self._visible_rows[display_index]
        elif key == Qt.Key_Home:
            col = 0
        elif key == Qt.Key_End:
            col = sheet.ncols - 1
        else:
            super().keyPressEvent(event)
            return
        self.scroll_to_cell(row, col)
        event.accept()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(event.rect(), COLOR_BACKGROUND)
        sheet = self._sheet
        if sheet is None or sheet.nrows <= 0 or sheet.ncols <= 0:
            self._paint_empty_message(painter, "无表格数据")
            painter.end()
            return
        if self._only_diffs and not self._visible_rows:
            self._paint_headers(painter)
            self._paint_empty_message(painter, "当前工作表没有差异")
            painter.end()
            return

        self._paint_cells(painter, event)
        self._paint_headers(painter)
        painter.end()

    def _paint_empty_message(self, painter: QPainter, text: str) -> None:
        painter.setPen(QColor("#6E7781"))
        font = QFont(self.font())
        font.setPointSizeF(11.0)
        painter.setFont(font)
        painter.drawText(
            QRectF(HEADER_WIDTH, HEADER_HEIGHT, max(1, self.viewport().width()), max(1, self.viewport().height())),
            int(Qt.AlignCenter),
            text,
        )

    def _visible_col_range(self) -> tuple[int, int]:
        sheet = self._sheet
        if sheet is None:
            return 0, 0
        left = self.horizontalScrollBar().value()
        right = left + self.viewport().width()
        first = max(0, bisect_right(self._col_offsets, left) - 1)
        last = min(sheet.ncols - 1, bisect_right(self._col_offsets, right))
        return first, last

    def _visible_row_range(self) -> tuple[int, int]:
        if not self._visible_rows:
            return 0, -1
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()
        first = max(0, bisect_right(self._row_offsets, top) - 1)
        last = min(len(self._visible_rows) - 1, bisect_right(self._row_offsets, bottom))
        return first, last

    def _paint_cells(self, painter: QPainter, event) -> None:
        sheet = self._sheet
        assert sheet is not None
        viewport = self.viewport()
        hscroll = self.horizontalScrollBar().value()
        vscroll = self.verticalScrollBar().value()
        content_clip = QRectF(
            HEADER_WIDTH,
            HEADER_HEIGHT,
            max(0, viewport.width() - HEADER_WIDTH),
            max(0, viewport.height() - HEADER_HEIGHT),
        )
        painter.save()
        painter.setClipRect(content_clip)
        painter.setClipRect(event.rect(), Qt.IntersectClip)

        first_row, last_row = self._visible_row_range()
        first_col, last_col = self._visible_col_range()

        # 先画合并单元格锚点（整体跨格）。
        for row, col, row_span, col_span in self._anchors:
            display_index = self._row_display.get(row)
            if display_index is None:
                continue
            if not (first_row <= display_index <= last_row and first_col <= col <= last_col):
                continue
            x = HEADER_WIDTH + self._col_offsets[col] - hscroll
            y = HEADER_HEIGHT + self._row_offsets[display_index] - vscroll
            width = self._col_offsets[min(sheet.ncols, col + col_span)] - self._col_offsets[col]
            height = self._row_height_sum(row, row_span)
            rect = QRectF(x, y, width, height)
            if rect.intersects(content_clip):
                self._paint_cell(painter, rect, sheet.rows[row][col], (row, col))

        for display_index in range(first_row, last_row + 1):
            row = self._visible_rows[display_index]
            y = HEADER_HEIGHT + self._row_offsets[display_index] - vscroll
            height = self._row_offsets[display_index + 1] - self._row_offsets[display_index]
            for col in range(first_col, last_col + 1):
                position = (row, col)
                if position in self._covered:
                    continue
                cell = sheet.rows[row][col]
                if cell.span is not None:
                    continue
                x = HEADER_WIDTH + self._col_offsets[col] - hscroll
                width = self._col_offsets[col + 1] - self._col_offsets[col]
                rect = QRectF(x, y, width, height)
                if rect.right() < content_clip.left() or rect.left() > content_clip.right():
                    continue
                self._paint_cell(painter, rect, cell, (row, col))
        painter.restore()

    def _cell_text_color(self, cell: Cell, diff: DiffItem | None) -> QColor:
        """深色主题下按实际底色选择文字颜色，避免源文件深色字落在深背景上。"""
        p = theme.palette()
        if diff is not None:
            uncertain = diff.severity == Severity.UNCERTAIN or diff.needs_review
            return QColor(p.warning_text if uncertain else p.diff_red_text)
        if p.dark:
            fill = QColor(cell.style.fill_color) if cell.style.fill_color else QColor(p.card_bg)
            return QColor(p.text) if fill.lightness() < 150 else QColor("#1B1B1B")
        return QColor(cell.style.font_color)

    def _paint_cell(self, painter: QPainter, rect: QRectF, cell: Cell, position: tuple[int, int]) -> None:
        diff = self._diff_map.get(position)
        if diff is not None:
            uncertain = diff.severity == Severity.UNCERTAIN or diff.needs_review
            painter.fillRect(rect, COLOR_DIFF_ORANGE_BG if uncertain else COLOR_DIFF_RED_BG)
        elif cell.style.fill_color:
            painter.fillRect(rect, QColor(cell.style.fill_color))
        else:
            painter.fillRect(rect, COLOR_BACKGROUND)

        if self._show_grid or diff is not None:
            painter.setPen(QPen(COLOR_GRID, 1))
            painter.drawRect(rect)

        if diff is not None:
            border = COLOR_DIFF_ORANGE_BORDER if (
                diff.severity == Severity.UNCERTAIN or diff.needs_review
            ) else COLOR_DIFF_RED_BORDER
            painter.setPen(QPen(border, 2))
            painter.drawRect(rect.adjusted(1, 1, -1, -1))
            painter.setPen(QPen(border, 3))
            painter.drawLine(rect.topLeft(), rect.bottomLeft())

        if self._selected is not None and tuple(self._selected) == position:
            painter.setPen(QPen(COLOR_SELECTED, 2))
            painter.drawRect(rect.adjusted(1, 1, -1, -1))

        display = cell.display
        if not display:
            return
        font = self._cell_font(cell)
        painter.setFont(font)
        painter.setPen(self._cell_text_color(cell, diff))
        text_rect = rect.adjusted(3, 1, -3, -1)
        flags = self._text_flags(cell)
        if cell.style.wrap:
            painter.drawText(text_rect, flags | int(Qt.TextWordWrap), display)
        else:
            metrics = QFontMetricsF(font)
            available = max(0.0, text_rect.width())
            elided = metrics.elidedText(display, Qt.ElideRight, available)
            painter.drawText(text_rect, flags, elided)

    def _cell_font(self, cell: Cell) -> QFont:
        font = QFont(self.font())
        font.setBold(bool(cell.style.bold))
        font.setItalic(bool(cell.style.italic))
        size = float(cell.style.font_size or 11.0)
        font.setPointSizeF(max(1.0, size * self._zoom))
        return font

    @staticmethod
    def _text_flags(cell: Cell) -> int:
        horizontal = {
            "left": Qt.AlignLeft,
            "center": Qt.AlignHCenter,
            "right": Qt.AlignRight,
        }.get(cell.style.h_align, Qt.AlignLeft)
        vertical = {
            "top": Qt.AlignTop,
            "center": Qt.AlignVCenter,
            "bottom": Qt.AlignBottom,
        }.get(cell.style.v_align, Qt.AlignVCenter)
        return int(horizontal | vertical)

    def _paint_headers(self, painter: QPainter) -> None:
        sheet = self._sheet
        viewport = self.viewport()
        hscroll = self.horizontalScrollBar().value()
        vscroll = self.verticalScrollBar().value()
        header_font = QFont(self.font())
        header_font.setPointSizeF(9.0)
        header_font.setBold(False)

        # 左上角
        corner = QRectF(0, 0, HEADER_WIDTH, HEADER_HEIGHT)
        painter.fillRect(corner, COLOR_HEADER_BG)
        painter.setPen(QPen(COLOR_GRID, 1))
        painter.drawRect(corner)

        if sheet is not None and sheet.ncols > 0:
            from ..spreadsheet.loader import column_letter

            first_col, last_col = self._visible_col_range()
            for col in range(first_col, last_col + 1):
                x = HEADER_WIDTH + self._col_offsets[col] - hscroll
                width = self._col_offsets[col + 1] - self._col_offsets[col]
                rect = QRectF(x, 0, width, HEADER_HEIGHT)
                if rect.right() < HEADER_WIDTH or rect.left() > viewport.width():
                    continue
                selected_col = self._selected is not None and self._selected[1] == col
                painter.fillRect(rect, COLOR_HEADER_HIGHLIGHT if selected_col else COLOR_HEADER_BG)
                painter.setPen(QPen(COLOR_GRID, 1))
                painter.drawRect(rect)
                painter.setFont(header_font)
                painter.setPen(COLOR_HEADER_TEXT)
                painter.drawText(rect, int(Qt.AlignCenter), column_letter(col))

        if sheet is not None and self._visible_rows:
            for display_index in range(self._visible_row_range()[0], self._visible_row_range()[1] + 1):
                row = self._visible_rows[display_index]
                y = HEADER_HEIGHT + self._row_offsets[display_index] - vscroll
                height = self._row_offsets[display_index + 1] - self._row_offsets[display_index]
                rect = QRectF(0, y, HEADER_WIDTH, height)
                if rect.bottom() < HEADER_HEIGHT or rect.top() > viewport.height():
                    continue
                selected_row = self._selected is not None and self._selected[0] == row
                painter.fillRect(rect, COLOR_HEADER_HIGHLIGHT if selected_row else COLOR_HEADER_BG)
                painter.setPen(QPen(COLOR_GRID, 1))
                painter.drawRect(rect)
                painter.setFont(header_font)
                painter.setPen(COLOR_HEADER_TEXT)
                painter.drawText(rect.adjusted(0, 0, -4, 0), int(Qt.AlignRight | Qt.AlignVCenter), str(row + 1))

        # 表头与内容区的分隔线
        painter.setPen(QPen(COLOR_GRID, 1))
        painter.drawLine(int(HEADER_WIDTH), 0, int(HEADER_WIDTH), viewport.height())
        painter.drawLine(0, int(HEADER_HEIGHT), viewport.width(), int(HEADER_HEIGHT))

    # ------------------------------------------------------------------
    # 便利属性
    # ------------------------------------------------------------------

    def diff_at(self, row: int, col: int) -> DiffItem | None:
        return self._diff_map.get((row, col))

    def selected_position(self) -> QPoint | None:
        if self._selected is None:
            return None
        return QPoint(self._selected[1], self._selected[0])


__all__ = ["GridView"]
