"""左右两侧容器：文件头、内容占位、拖拽导入。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..file_loader import FileInfo, SUPPORTED_EXTS
from . import theme


class SidePane(QFrame):
    file_dropped = Signal(str)
    clicked = Signal(str)

    def __init__(self, side: str, parent=None):
        super().__init__(parent)
        self.side = side
        self._content: QWidget | None = None
        self.setObjectName("sidePane")
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(
            "QFrame#sidePane { background:#FFFFFF; border:1px solid #E0E0E0; border-radius:8px; }"
            "QFrame#sidePane[dragActive=\"true\"] { border:2px solid #0067C0; background:#F7FBFF; }"
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("sideHeader")
        self._header = header
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(8)
        self.name_label = QLabel("未选择文件")
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.meta_label = QLabel("")
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.name_label, 1)
        h.addWidget(self.meta_label)
        self._layout.addWidget(header)

        self._stack = QStackedWidget()
        self._empty = QLabel()
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setText(
            "<div style='font-size:15pt;color:#1B1B1B;margin-bottom:10px;'>将文件拖到此处</div>"
            "<div style='font-size:10pt;line-height:1.7;color:#616161;'>松手后立即加载并开始比较（图片会先显示原图，再执行 OCR）<br>"
            "支持 TXT / CSV / XLS / XLSX / PNG / JPG / JPEG / BMP<br>"
            "也可以点击上方“选择文件”或 Ctrl+V 粘贴截图</div>"
        )
        self._stack.addWidget(self._empty)
        self._layout.addWidget(self._stack, 1)
        self._info: FileInfo | None = None
        self.apply_theme()

    # ------------------------------------------------------------------
    def apply_theme(self, palette=None) -> None:
        p = palette or theme.palette()
        self.setStyleSheet(
            f"QFrame#sidePane {{ background:{p.card_bg}; border:1px solid {p.divider}; border-radius:8px; }}"
            f"QFrame#sidePane[dragActive=\"true\"] {{ border:2px solid {p.accent}; background:{p.accent_soft}; }}"
        )
        self._header.setStyleSheet(
            f"QWidget#sideHeader {{ background:{p.secondary_surface}; border-bottom:1px solid {p.divider};"
            "border-top-left-radius:8px; border-top-right-radius:8px; }"
        )
        self.name_label.setStyleSheet(f"font-weight:600;color:{p.text};")
        self.meta_label.setStyleSheet(f"color:{p.text_secondary};font-size:9pt;")
        self._empty.setStyleSheet(
            f"color:{p.text_secondary};background:{p.card_bg};padding:28px;"
        )
        # 保留现有提示结构，只替换浅色主题中的文字色，深色下保持可读
        html = (
            self._empty.text()
            .replace("#1B1B1B", p.text)
            .replace("#616161", p.text_secondary)
            .replace("#757575", p.text_secondary)
        )
        self._empty.setText(html)

    def set_file(self, info: FileInfo | None) -> None:
        self._info = info
        if info is None:
            self.name_label.setText("未选择文件")
            self.meta_label.setText("")
            self.setToolTip("")
            return
        self.name_label.setText(info.name)
        self.name_label.setToolTip(info.path)
        self.meta_label.setText(f"{info.type_label} · {info.human_size}")
        self.setToolTip(info.path)

    def set_meta(self, text: str) -> None:
        self.meta_label.setText(text)

    def set_content(self, widget: QWidget | None, empty_text: str | None = None) -> None:
        if self._content is not None:
            self._stack.removeWidget(self._content)
            self._content.deleteLater()
            self._content = None
        if widget is None:
            if empty_text is not None:
                self.set_empty_text(empty_text)
            self._stack.setCurrentWidget(self._empty)
            return
        self._content = widget
        self._stack.addWidget(widget)
        self._stack.setCurrentWidget(widget)
        self._install_drop_proxy(widget)

    def set_empty_text(self, text: str) -> None:
        p = theme.palette()
        self._empty.setText(
            f"<div style='font-size:11pt;color:{p.text_secondary};line-height:1.7;'>{text}</div>"
        )

    def content(self) -> QWidget | None:
        return self._content

    def show_placeholder(self, text: str) -> None:
        self.set_content(None, text)

    # ------------------------------------------------------------------
    def _install_drop_proxy(self, root: QWidget) -> None:
        """让内容控件（QGraphicsView/TextView/GridView 等）上的拖拽也落到本侧。

        子控件通常自己消费 Drag/Drop 事件，导致外层 SidePane 收不到；这里对内容
        及所有后代的拖拽事件安装过滤器，统一交给 SidePane 处理。文件拖入后由
        MainWindow 立即触发比较。
        """
        widgets = [root, *root.findChildren(QWidget)]
        for widget in widgets:
            try:
                widget.setAcceptDrops(True)
                widget.installEventFilter(self)
            except RuntimeError:
                continue

    @staticmethod
    def _first_supported_path(mime) -> str | None:
        if mime is None or not mime.hasUrls():
            return None
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if Path(path).suffix.lower() in SUPPORTED_EXTS:
                return path
        return None

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        etype = event.type()
        if etype in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if self._first_supported_path(event.mimeData()) is not None:
                event.acceptProposedAction()
                self._set_drag_active(True)
                return True
        elif etype == QEvent.Type.DragLeave:
            self._set_drag_active(False)
        elif etype == QEvent.Type.Drop:
            path = self._first_supported_path(event.mimeData())
            self._set_drag_active(False)
            if path is not None:
                self.file_dropped.emit(path)
                event.acceptProposedAction()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        self.clicked.emit(self.side)
        super().mousePressEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):  # noqa: N802
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    ext = Path(url.toLocalFile()).suffix.lower()
                    if ext in SUPPORTED_EXTS:
                        event.acceptProposedAction()
                        self._set_drag_active(True)
                        return
        event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802
        self._set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent):  # noqa: N802
        self._set_drag_active(False)
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if Path(path).suffix.lower() in SUPPORTED_EXTS:
                        self.file_dropped.emit(path)
                        event.acceptProposedAction()
                        return
        event.ignore()

    def _set_drag_active(self, active: bool) -> None:
        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)
