"""图片批量比较的“原图对照”窗口：基准 + 任选候选，保留原图与差异框。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..batch import BatchResult
from . import theme
from .image_view import ImagePane


class BatchImageDialog(QDialog):
    def __init__(self, result: BatchResult, parent=None, candidate_index: int = 0,
                 reveal_diff=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("EngineerDiff - 原图对照")
        self.resize(1320, 820)
        self.setMinimumSize(900, 600)
        self._result = result
        self._reveal_diff = reveal_diff
        self._build_ui()
        self.apply_theme()
        idx = max(0, min(candidate_index, len(result.candidates) - 1))
        self.combo.setCurrentIndex(idx)
        self._load_candidate(idx)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("原图对照")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addSpacing(12)
        head.addWidget(QLabel("候选文件："))
        self.combo = QComboBox()
        for cand in self._result.candidates:
            self.combo.addItem(cand.name, cand.path)
        self.combo.currentIndexChanged.connect(self._load_candidate)
        head.addWidget(self.combo, 1)
        self.cb_sync = QCheckBox("同步缩放")
        self.cb_sync.setChecked(True)
        head.addWidget(self.cb_sync)
        self.btn_fit = QPushButton("适应窗口")
        self.btn_fit.setObjectName("tool")
        self.btn_fit.clicked.connect(self._fit)
        head.addWidget(self.btn_fit)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.reject)
        head.addWidget(self.btn_close)
        root.addLayout(head)

        self.lbl_hint = QLabel("滚轮直接缩放，拖动平移；左侧为基准，右侧为所选候选。红色框为该候选相对基准的差异。")
        self.lbl_hint.setObjectName("subtle")
        root.addWidget(self.lbl_hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left_pane = ImagePane()
        self.right_pane = ImagePane()
        splitter.addWidget(self.left_pane)
        splitter.addWidget(self.right_pane)
        splitter.setSizes([650, 650])
        root.addWidget(splitter, 1)

        self.left_pane.zoom_changed.connect(self._on_left_zoom)
        self.right_pane.zoom_changed.connect(self._on_right_zoom)
        self.left_pane.set_sync_zoom(True)
        self.right_pane.set_sync_zoom(True)
        self._syncing = False

    def _on_left_zoom(self, zoom: float) -> None:
        if self.cb_sync.isChecked() and not self._syncing:
            self._syncing = True
            self.right_pane.set_zoom(zoom)
            self._syncing = False

    def _on_right_zoom(self, zoom: float) -> None:
        if self.cb_sync.isChecked() and not self._syncing:
            self._syncing = True
            self.left_pane.set_zoom(zoom)
            self._syncing = False

    def _load_candidate(self, index: int) -> None:
        if index < 0 or index >= len(self._result.candidates):
            return
        cand = self._result.candidates[index]
        if cand.result is None or cand.result.image is None:
            return
        image = cand.result.image
        self.left_pane.set_comparison(image.left, image, "left")
        self.right_pane.set_comparison(image.right, image, "right")
        self.left_pane.setToolTip(f"基准：{Path(self._result.baseline).name}")
        self.right_pane.setToolTip(cand.path)
        if self._reveal_diff is not None:
            self.left_pane.reveal(self._reveal_diff)
            self.right_pane.reveal(self._reveal_diff)

    def _fit(self) -> None:
        self.left_pane.fit_to_window()
        self.right_pane.fit_to_window()

    def apply_theme(self) -> None:
        p = theme.palette()
        self.lbl_hint.setStyleSheet(f"color:{p.text_secondary};")
        self.left_pane.apply_theme()
        self.right_pane.apply_theme()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._fit()
