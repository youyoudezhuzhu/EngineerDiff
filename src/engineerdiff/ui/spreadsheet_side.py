"""左右两侧表格面板：GridView + 底部 Sheet 标签 + 占位提示。

契约见 docs/ui-contracts.md 2.4：

* 标签颜色：当前 Sheet 有差异 / 仅一侧存在 → 红色；含橙色项 → 橙红；
  选中标签加下划线并加粗，与红色区分（选中背景 #E3F2FD）。
* 用户点击标签发 ``sheet_selected(name)``；``show_sheet(name)`` 只显示，
  不反向发信号（同步逻辑由 MainWindow 负责，避免递归）。
* 本侧没有该名称时显示“无对应工作表”占位，保留标签选中态。
* 空工作簿显示“工作簿中没有工作表”。
* ``scroll_changed(垂直比例, 水平比例)`` 供 MainWindow 做滚动同步。
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Iterable

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models import DiffItem, SheetStatus, Workbook, WorkbookComparison
from . import theme
from .grid_view import GridView

TAB_HEIGHT = 34
TAB_MIN_WIDTH = 64
TAB_MAX_WIDTH = 200

TAB_COLOR_NORMAL = QColor("#616161")
TAB_COLOR_DIFF = QColor("#C62828")
TAB_COLOR_UNCERTAIN = QColor("#E65100")
TAB_BACKGROUND = QColor("#FAFAFA")
TAB_SELECTED_BACKGROUND = QColor("#E5F1FB")
TAB_SELECTED_UNDERLINE = QColor("#0067C0")


def refresh_palette() -> None:
    global TAB_COLOR_NORMAL, TAB_COLOR_DIFF, TAB_COLOR_UNCERTAIN
    global TAB_BACKGROUND, TAB_SELECTED_BACKGROUND, TAB_SELECTED_UNDERLINE
    p = theme.palette()
    TAB_COLOR_NORMAL = QColor(p.text_secondary)
    TAB_COLOR_DIFF = QColor(p.diff_red)
    TAB_COLOR_UNCERTAIN = QColor(p.warning)
    TAB_BACKGROUND = QColor(p.secondary_surface)
    TAB_SELECTED_BACKGROUND = QColor(p.accent_soft)
    TAB_SELECTED_UNDERLINE = QColor(p.accent)


class SheetTabBar(QWidget):
    """底部 Sheet 标签栏（自绘，便于精确控制红色/橙红文字）。

    支持鼠标滚轮、按住拖动、左右箭头三种方式横向滚动；选中标签会自动滚入
    可视区域，避免工作表很多时选不到后面的表。
    """

    tab_clicked = Signal(str)
    overflow_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(TAB_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._names: list[str] = []
        self._status: dict[str, str] = {}
        self._selected: str | None = None
        self._offset = 0
        self._rects: list[tuple[str, QRect]] = []
        self._drag_start_x = 0
        self._drag_start_offset = 0
        self._dragging = False
        self._drag_moved = False

    # ------------------------------------------------------------------
    def set_tabs(
        self,
        names: Iterable[str],
        red_names: Iterable[str] = (),
        orange_names: Iterable[str] = (),
    ) -> None:
        self._names = list(names)
        red = set(red_names)
        orange = set(orange_names)
        self._status = {}
        for name in self._names:
            if name in orange:
                self._status[name] = "uncertain"
            elif name in red:
                self._status[name] = "diff"
            else:
                self._status[name] = "normal"
        if self._selected not in self._names:
            self._selected = self._names[0] if self._names else None
        self._offset = max(0, min(self._offset, max(0, self._total_width() - self.width())))
        self._ensure_selected_visible()
        self.overflow_changed.emit(self._overflow() > 0)
        self.update()

    def names(self) -> list[str]:
        return list(self._names)

    def set_selected(self, name: str | None) -> None:
        self._selected = name
        self._ensure_selected_visible()
        self.update()

    def _overflow(self) -> int:
        return max(0, self._total_width() - self.width())

    def has_overflow(self) -> bool:
        return self._overflow() > 0

    def scroll_step(self, direction: int, fraction: float = 0.82) -> None:
        """向左/右横向移动一屏；direction=-1 左，+1 右。"""
        if self._overflow() <= 0:
            return
        page = max(48, int(self.width() * fraction))
        self._offset = max(0, min(self._overflow(), self._offset + direction * page))
        self.update()

    def _ensure_selected_visible(self) -> None:
        if self._selected not in self._names or self._overflow() <= 0:
            return
        self._layout()
        for name, rect in self._rects:
            if name != self._selected:
                continue
            if rect.left() < 0:
                self._offset = max(0, self._offset + rect.left())
            elif rect.right() > self.width():
                self._offset = min(self._overflow(), self._offset + rect.right() - self.width())
            return

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._offset = max(0, min(self._offset, self._overflow()))
        self._ensure_selected_visible()
        self.overflow_changed.emit(self._overflow() > 0)

    def selected(self) -> str | None:
        return self._selected

    def color_for(self, name: str) -> QColor:
        status = self._status.get(name, "normal")
        if status == "uncertain":
            return TAB_COLOR_UNCERTAIN
        if status == "diff":
            return TAB_COLOR_DIFF
        return TAB_COLOR_NORMAL

    def tab_rect(self, name: str) -> QRect:
        self._layout()
        for tab_name, rect in self._rects:
            if tab_name == name:
                return rect
        return QRect()

    # ------------------------------------------------------------------
    def _font(self, bold: bool = False) -> QFont:
        font = QFont(self.font())
        font.setPointSizeF(9.5)
        font.setBold(bold)
        return font

    def _tab_width(self, name: str) -> int:
        metrics = QFontMetrics(self._font())
        return max(TAB_MIN_WIDTH, min(TAB_MAX_WIDTH, metrics.horizontalAdvance(name) + 28))

    def _total_width(self) -> int:
        return sum(self._tab_width(name) for name in self._names)

    def _layout(self) -> list[tuple[str, QRect]]:
        if self._total_width() <= self.width():
            self._offset = 0
        self._rects = []
        x = -self._offset
        for name in self._names:
            width = self._tab_width(name)
            self._rects.append((name, QRect(x, 1, width, self.height() - 2)))
            x += width
        return self._rects

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), TAB_BACKGROUND)
        painter.setPen(QPen(QColor("#D0D7DE"), 1))
        painter.drawLine(0, 0, self.width(), 0)

        rects = self._layout()
        for name, rect in rects:
            if rect.right() < 0 or rect.left() > self.width():
                continue
            selected = name == self._selected
            if selected:
                painter.fillRect(rect.adjusted(1, 1, -1, 0), TAB_SELECTED_BACKGROUND)
            font = self._font(bold=selected)
            painter.setFont(font)
            painter.setPen(self.color_for(name))
            text_rect = rect.adjusted(14, 0, -14, 0)
            if text_rect.width() <= 0:
                continue
            elided = QFontMetrics(font).elidedText(name, Qt.ElideRight, text_rect.width())
            painter.drawText(text_rect, int(Qt.AlignCenter), elided)
            if selected:
                painter.setPen(QPen(TAB_SELECTED_UNDERLINE, 2))
                painter.drawLine(rect.left() + 6, rect.bottom() - 1, rect.right() - 6, rect.bottom() - 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton:
            self._drag_start_x = event.position().toPoint().x()
            self._drag_start_offset = self._offset
            self._dragging = True
            self._drag_moved = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._dragging and self._overflow() > 0:
            delta = event.position().toPoint().x() - self._drag_start_x
            if abs(delta) > 3:
                self._drag_moved = True
            self._offset = max(0, min(self._overflow(), self._drag_start_offset - delta))
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if not self._drag_moved:
                position = event.position().toPoint()
                for name, rect in self._layout():
                    if rect.contains(position):
                        self.tab_clicked.emit(name)
                        break
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        overflow = self._overflow()
        if overflow <= 0:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.angleDelta().x()
        step = max(48, abs(delta) // 2 if delta else 48)
        direction = -1 if delta > 0 else 1
        self._offset = max(0, min(overflow, self._offset + direction * step))
        self.update()
        event.accept()


class SpreadsheetSide(QWidget):
    """一侧的表格视图（GridView + Sheet 标签 + 占位）。"""

    sheet_selected = Signal(str)
    scroll_changed = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = GridView(self)
        self._placeholder = QLabel("", self)
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet("color: #6E7781; font-size: 15px; background: #FFFFFF;")
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._grid)
        self._stack.addWidget(self._placeholder)

        self._tab_bar = SheetTabBar(self)
        self._tab_bar.tab_clicked.connect(self._on_tab_clicked)

        self._tab_left = QPushButton("‹")
        self._tab_left.setObjectName("tool")
        self._tab_left.setFixedSize(24, TAB_HEIGHT - 2)
        self._tab_left.setToolTip("向左滚动工作表标签（也可在标签上滚轮/拖动）")
        self._tab_right = QPushButton("›")
        self._tab_right.setObjectName("tool")
        self._tab_right.setFixedSize(24, TAB_HEIGHT - 2)
        self._tab_right.setToolTip("向右滚动工作表标签（也可在标签上滚轮/拖动）")
        self._tab_left.clicked.connect(lambda: self._tab_bar.scroll_step(-1))
        self._tab_right.clicked.connect(lambda: self._tab_bar.scroll_step(1))
        tab_row = QWidget()
        tab_layout = QHBoxLayout(tab_row)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)
        tab_layout.addWidget(self._tab_left)
        tab_layout.addWidget(self._tab_bar, 1)
        tab_layout.addWidget(self._tab_right)
        self._tab_row = tab_row
        self._tab_bar.overflow_changed.connect(self._update_tab_arrows)
        self._update_tab_arrows(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._stack, 1)
        layout.addWidget(tab_row, 0)

        self._workbook: Workbook | None = None
        self._comparison: WorkbookComparison | None = None
        self._side = "left"
        self._selected: str | None = None
        self._placeholder_text = ""
        self._suppress_scroll_signal = False

        self._grid.verticalScrollBar().valueChanged.connect(self._emit_scroll)
        self._grid.horizontalScrollBar().valueChanged.connect(self._emit_scroll)

    # ------------------------------------------------------------------
    # 契约接口
    # ------------------------------------------------------------------

    def load(
        self,
        workbook: Workbook,
        comparison: WorkbookComparison | None,
        side: str,
        show_tabs: bool = True,
    ) -> None:
        self._workbook = workbook
        self._comparison = comparison
        self._side = "right" if side == "right" else "left"
        self._tab_bar.setVisible(bool(show_tabs))
        names = self._tab_names()
        red_names: set[str] = set()
        orange_names: set[str] = set()
        if comparison is not None:
            for sheet_comparison in comparison.sheets:
                if sheet_comparison.status != SheetStatus.EQUAL:
                    red_names.add(sheet_comparison.name)
                if any(
                    diff.severity.value == "uncertain" or diff.needs_review
                    for diff in sheet_comparison.diffs
                ):
                    orange_names.add(sheet_comparison.name)
        self._tab_bar.set_tabs(names, red_names, orange_names)
        if workbook is not None and workbook.sheets:
            initial = workbook.sheets[0].name
        elif names:
            initial = names[0]
        else:
            initial = None
        self.show_sheet(initial)

    def show_sheet(self, name: str | None) -> None:
        self._selected = name
        self._tab_bar.set_selected(name)
        self._suppress_scroll_signal = True
        try:
            if self._workbook is None or not self._workbook.sheets:
                self._show_placeholder("工作簿中没有工作表")
                return
            if name is None:
                self._show_placeholder("工作簿中没有工作表")
                return
            sheet = self._workbook.sheet(name)
            if sheet is None:
                self._show_placeholder("无对应工作表")
                return
            diffs: list[DiffItem] = []
            if self._comparison is not None:
                sheet_comparison = self._comparison.sheet_by_name(name)
                if sheet_comparison is not None:
                    diffs = list(sheet_comparison.diffs)
            self._grid.set_sheet(sheet, diffs, self._side)
            self._stack.setCurrentWidget(self._grid)
        finally:
            self._suppress_scroll_signal = False

    def current_sheet(self) -> str | None:
        return self._selected

    def sheet_names(self) -> list[str]:
        return self._tab_bar.names()

    @staticmethod
    def _diff_sheet_name(diff: DiffItem) -> str | None:
        """DiffItem 本身没有 sheet 字段，工作表名来自两侧 DiffLocation。"""
        for location in (diff.left, diff.right):
            if location is not None and location.sheet:
                return location.sheet
        return None

    def reveal(self, diff: DiffItem) -> None:
        name = self._diff_sheet_name(diff)
        if name:
            self.show_sheet(name)
        if self._workbook is None or not self._workbook.sheets or name is None:
            return
        if self._workbook.sheet(name) is None:
            return
        location = diff.left if self._side == "left" else diff.right
        if location is None or location.row is None or location.col is None:
            return
        self._grid.scroll_to_cell(location.row, location.col)

    def set_zoom(self, zoom: float) -> None:
        self._grid.set_zoom(zoom)

    def set_only_diffs(self, enabled: bool) -> None:
        self._grid.set_only_diffs(enabled)

    def grid(self) -> GridView:
        return self._grid

    # ------------------------------------------------------------------
    # 额外辅助接口（MainWindow / 测试使用）
    # ------------------------------------------------------------------

    def tab_bar(self) -> SheetTabBar:
        return self._tab_bar

    def tab_color(self, name: str) -> QColor:
        return self._tab_bar.color_for(name)

    def is_placeholder_visible(self) -> bool:
        return self._stack.currentWidget() is self._placeholder

    def placeholder_text(self) -> str:
        return self._placeholder.text()

    def set_scroll_ratio(self, vertical: float, horizontal: float) -> None:
        """按比例设置滚动位置（供 MainWindow 同步滚动使用）。"""
        vbar = self._grid.verticalScrollBar()
        hbar = self._grid.horizontalScrollBar()
        self._suppress_scroll_signal = True
        try:
            vbar.setValue(int(round(max(0.0, min(1.0, vertical)) * vbar.maximum())))
            hbar.setValue(int(round(max(0.0, min(1.0, horizontal)) * hbar.maximum())))
        finally:
            self._suppress_scroll_signal = False

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _update_tab_arrows(self, has_overflow: bool) -> None:
        self._tab_left.setVisible(bool(has_overflow))
        self._tab_right.setVisible(bool(has_overflow))
        if has_overflow:
            self._tab_left.setEnabled(True)
            self._tab_right.setEnabled(True)

    def _tab_names(self) -> list[str]:
        if self._comparison is not None:
            return [sheet_comparison.name for sheet_comparison in self._comparison.sheets]
        if self._workbook is not None:
            return [sheet.name for sheet in self._workbook.sheets]
        return []

    def _on_tab_clicked(self, name: str) -> None:
        self.show_sheet(name)
        self.sheet_selected.emit(name)

    def _show_placeholder(self, text: str) -> None:
        self._placeholder_text = text
        self._placeholder.setText(text)
        self._grid.set_sheet(None, [], self._side)
        self._stack.setCurrentWidget(self._placeholder)

    def _emit_scroll(self) -> None:
        if self._suppress_scroll_signal:
            return
        vbar = self._grid.verticalScrollBar()
        hbar = self._grid.horizontalScrollBar()
        vertical = vbar.value() / vbar.maximum() if vbar.maximum() > 0 else 0.0
        horizontal = hbar.value() / hbar.maximum() if hbar.maximum() > 0 else 0.0
        self.scroll_changed.emit(vertical, horizontal)


__all__ = ["SheetTabBar", "SpreadsheetSide"]
