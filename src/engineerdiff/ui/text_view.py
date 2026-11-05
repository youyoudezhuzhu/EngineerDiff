"""文本双栏差异渲染：自绘、虚拟化、字符级标红。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea

from ..models import DiffItem, DisplayLine, LineStatus, TextSide
from . import theme


class TextView(QAbstractScrollArea):
    """单侧文本视图。行高固定，只绘制视口内行，适合大文件。"""

    line_activated = Signal(int)  # display row

    def __init__(self, parent=None):
        super().__init__(parent)
        self._side: TextSide | None = None
        self._only_diffs = False
        self._visible: list[int] = []
        self._current_id: str | None = None
        self._empty_text = "请选择文件"
        self._font = theme.mono_font(10.0)
        self.setFont(self._font)
        self.viewport().setFont(self._font)
        self._fm = QFontMetrics(self._font)
        self._line_h = max(18, self._fm.height() + 6)
        self._gutter = 52
        self._max_text_w = 0
        self.viewport().setAutoFillBackground(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.horizontalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.setFrameShape(self.Shape.NoFrame)
        self._recalc_gutter()

    # ------------------------------------------------------------------ API
    def set_side(self, side: TextSide | None, empty_text: str = "请选择文件") -> None:
        self._side = side
        self._empty_text = empty_text
        self._current_id = None
        self.verticalScrollBar().setValue(0)
        self.horizontalScrollBar().setValue(0)
        self._rebuild()

    def side(self) -> TextSide | None:
        return self._side

    def set_only_diffs(self, enabled: bool) -> None:
        if enabled == self._only_diffs:
            return
        self._only_diffs = enabled
        pos = self.verticalScrollBar().value() // self._line_h
        self._rebuild(keep_pos=pos)

    def only_diffs(self) -> bool:
        return self._only_diffs

    def set_current_diff(self, diff_id: str | None) -> None:
        self._current_id = diff_id
        self.viewport().update()

    def reveal(self, diff: DiffItem | None, side: str) -> None:
        if diff is None or self._side is None:
            return
        loc = diff.left if side == "left" else diff.right
        row = None
        if loc is not None:
            row = loc.extra.get("display_row")
            if row is None and loc.line is not None:
                row = self._side.reveal_line(loc.line)
        if row is None:
            return
        if self._only_diffs:
            try:
                pos = self._visible.index(int(row))
            except ValueError:
                # 差异不在过滤结果中时临时回到全量定位
                pos = int(row)
        else:
            pos = int(row)
        target = max(0, pos * self._line_h - self._line_h * 2)
        self.verticalScrollBar().setValue(target)
        self.set_current_diff(diff.id)

    def reveal_line(self, line_no: int | None) -> None:
        if self._side is None or line_no is None:
            return
        row = self._side.reveal_line(line_no)
        if row is not None:
            self.verticalScrollBar().setValue(max(0, row * self._line_h - self._line_h * 2))
            self._current_id = None
            self.viewport().update()

    def scroll_ratio(self) -> float:
        bar = self.verticalScrollBar()
        span = bar.maximum() - bar.minimum()
        return (bar.value() - bar.minimum()) / span if span > 0 else 0.0

    def set_scroll_ratio(self, ratio: float) -> None:
        bar = self.verticalScrollBar()
        span = bar.maximum() - bar.minimum()
        if span > 0:
            bar.setValue(int(bar.minimum() + max(0.0, min(1.0, ratio)) * span))

    def horizontal_ratio(self) -> float:
        bar = self.horizontalScrollBar()
        span = bar.maximum() - bar.minimum()
        return (bar.value() - bar.minimum()) / span if span > 0 else 0.0

    def set_horizontal_ratio(self, ratio: float) -> None:
        bar = self.horizontalScrollBar()
        span = bar.maximum() - bar.minimum()
        if span > 0:
            bar.setValue(int(bar.minimum() + max(0.0, min(1.0, ratio)) * span))

    def visible_row_count(self) -> int:
        return len(self._visible)

    # ------------------------------------------------------------------ internals
    def _recalc_gutter(self) -> None:
        max_line = 1
        if self._side is not None:
            for line in self._side.lines:
                if line.line_no and line.line_no > max_line:
                    max_line = line.line_no
        digits = max(2, len(str(max_line)))
        self._gutter = self._fm.horizontalAdvance("0") * digits + 18

    def _rebuild(self, keep_pos: int = 0) -> None:
        self._recalc_gutter()
        if self._side is None:
            self._visible = []
            self._max_text_w = 0
        else:
            lines = self._side.lines
            if self._only_diffs:
                rows: set[int] = set()
                for i, line in enumerate(lines):
                    if line.diff_id is not None or line.status != LineStatus.EQUAL:
                        rows.update((i - 1, i, i + 1))
                self._visible = sorted(i for i in rows if 0 <= i < len(lines))
            else:
                self._visible = list(range(len(lines)))
            max_w = 0
            for line in lines:
                w = self._fm.horizontalAdvance(line.text)
                if w > max_w:
                    max_w = w
            self._max_text_w = max_w
        self._update_scrollbars(keep_pos)

    def _update_scrollbars(self, keep_pos: int = 0) -> None:
        vp = self.viewport().size()
        content_h = len(self._visible) * self._line_h
        vbar = self.verticalScrollBar()
        vbar.setPageStep(max(1, vp.height()))
        vbar.setSingleStep(self._line_h)
        vbar.setRange(0, max(0, content_h - vp.height()))
        vbar.setValue(min(vbar.maximum(), max(0, keep_pos * self._line_h)))
        text_view_w = max(1, vp.width() - self._gutter)
        hbar = self.horizontalScrollBar()
        hbar.setPageStep(max(1, text_view_w))
        hbar.setSingleStep(max(12, self._fm.averageCharWidth() * 4))
        hbar.setRange(0, max(0, self._max_text_w + 24 - text_view_w))
        hbar.setValue(0)
        self.viewport().update()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._update_scrollbars(self.verticalScrollBar().value() // self._line_h)

    def _line_at(self, y: int) -> int | None:
        pos = (self.verticalScrollBar().value() + y) // self._line_h
        if 0 <= pos < len(self._visible):
            return self._visible[pos]
        return None

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            row = self._line_at(event.position().toPoint().y())
            if row is not None and self._side is not None:
                line = self._side.lines[row]
                if line.diff_id is not None or line.status != LineStatus.EQUAL:
                    self.line_activated.emit(row)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        row = self._line_at(event.position().toPoint().y())
        if row is not None:
            self.line_activated.emit(row)
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------ wheel
    def _handle_horizontal_wheel(self, event) -> bool:
        """Shift+滚轮或触控板横向滚动；单行超长文本时普通滚轮也可横向滚动。"""
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

    def wheelEvent(self, event):  # noqa: N802
        if not self._handle_horizontal_wheel(event):
            super().wheelEvent(event)

    def viewportEvent(self, event):  # noqa: N802
        if event.type() == QEvent.Type.Wheel and self._handle_horizontal_wheel(event):
            return True
        return super().viewportEvent(event)

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self.viewport())
        p.fillRect(event.rect(), QColor(theme.PANEL_BG))
        vp = self.viewport().rect()
        if self._side is None or not self._visible:
            msg = self._empty_text if self._side is None else "仅显示差异：没有差异"
            p.setPen(QColor("#8A94A6"))
            p.setFont(theme.ui_font(10.5))
            p.drawText(vp, Qt.AlignmentFlag.AlignCenter, msg)
            return

        p.setFont(self._font)
        fm = self._fm
        scroll_y = self.verticalScrollBar().value()
        scroll_x = self.horizontalScrollBar().value()
        line_h = self._line_h
        first = max(0, scroll_y // line_h)
        last = min(len(self._visible) - 1, (scroll_y + vp.height()) // line_h + 1)
        text_x0 = self._gutter - scroll_x

        # 文本区域裁剪，避免画到行号槽
        p.save()
        p.setClipRect(QRect(self._gutter, 0, max(0, vp.width() - self._gutter), vp.height()))
        for pos in range(first, last + 1):
            row = self._visible[pos]
            line = self._side.lines[row]
            y = pos * line_h - scroll_y
            rect = QRect(0, y, vp.width(), line_h)
            if pos > 0 and self._visible[pos - 1] != row - 1:
                p.setPen(QPen(QColor("#D5DBE3"), 1, Qt.PenStyle.DashLine))
                p.drawLine(self._gutter + 2, y, vp.width() - 2, y)
            self._paint_line(p, line, rect, text_x0, line_h, fm)
        p.restore()

        # 行号槽
        p.fillRect(QRect(0, 0, self._gutter, vp.height()), QColor(theme.GUTTER_BG))
        p.setPen(QColor(theme.BORDER))
        p.drawLine(self._gutter - 1, 0, self._gutter - 1, vp.height())
        p.setFont(theme.mono_font(9.0))
        nfm = QFontMetrics(theme.mono_font(9.0))
        for pos in range(first, last + 1):
            row = self._visible[pos]
            line = self._side.lines[row]
            y = pos * line_h - scroll_y
            if line.status == LineStatus.PLACEHOLDER:
                label = ""
            else:
                label = str(line.line_no) if line.line_no is not None else ""
            is_current = self._current_id is not None and line.diff_id == self._current_id
            if is_current:
                p.fillRect(QRect(0, y, 3, line_h), QColor(theme.CURRENT_BLUE))
                p.setPen(QColor(theme.CURRENT_BLUE))
            elif line.is_changed:
                p.setPen(QColor(theme.RED))
            else:
                p.setPen(QColor(theme.GUTTER_TEXT))
            p.drawText(
                QRect(4, y, self._gutter - 8, line_h),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(0, vp.bottom(), vp.width(), vp.bottom())

    def _paint_line(self, p: QPainter, line: DisplayLine, rect: QRect, text_x: int, line_h: int, fm: QFontMetrics) -> None:
        if line.status == LineStatus.PLACEHOLDER:
            p.fillRect(rect, QColor(theme.PLACEHOLDER_BG))
            p.setPen(QColor("#AEB6C2"))
            p.setFont(theme.ui_font(8.5))
            p.drawText(QRect(text_x + 4, rect.top(), 60, line_h), Qt.AlignmentFlag.AlignVCenter, "空白")
            p.setFont(self._font)
            return

        bg = None
        if line.status in (LineStatus.ADDED, LineStatus.REMOVED):
            bg = QColor(theme.ADDED_BG)
        elif line.diff_id is not None and self._current_id == line.diff_id:
            bg = QColor(theme.CURRENT_BG)
        if bg is not None:
            p.fillRect(rect, bg)

        if line.is_changed:
            p.fillRect(QRect(0, rect.top(), 3, line_h), QColor(theme.RED))

        # 差异字符背景
        spans = [(sp.start, sp.end, sp.severity) for sp in line.spans]
        for start, end, sev in spans:
            if end <= start:
                continue
            x1 = text_x + fm.horizontalAdvance(line.text[:start])
            x2 = text_x + fm.horizontalAdvance(line.text[:end])
            color = theme.ORANGE_BG if sev.value == "uncertain" else theme.RED_BG
            p.fillRect(QRect(int(x1), rect.top() + 1, max(1, int(x2 - x1)), line_h - 2), QColor(color))
            p.fillRect(QRect(int(x1), rect.bottom() - 2, max(1, int(x2 - x1)), 1), QColor(theme.ORANGE if sev.value == "uncertain" else theme.RED))

        baseline = rect.top() + (line_h - fm.height()) // 2 + fm.ascent()
        if not spans:
            p.setPen(QColor(theme.TEXT))
            p.drawText(int(text_x), baseline, line.text)
            return

        # 分段绘制，差异字符红字；保持未变部分原色
        spans_sorted = sorted(spans)
        pos = 0
        x = text_x
        for start, end, sev in spans_sorted:
            start = max(pos, min(len(line.text), start))
            end = max(start, min(len(line.text), end))
            if pos < start:
                seg = line.text[pos:start]
                p.setPen(QColor(theme.TEXT))
                p.drawText(int(x), baseline, seg)
                x += fm.horizontalAdvance(seg)
            if end > start:
                seg = line.text[start:end]
                p.setPen(QColor(theme.ORANGE_TEXT if sev.value == "uncertain" else theme.RED_TEXT))
                p.drawText(int(x), baseline, seg)
                x += fm.horizontalAdvance(seg)
            pos = end
        if pos < len(line.text):
            p.setPen(QColor(theme.TEXT))
            p.drawText(int(x), baseline, line.text[pos:])
