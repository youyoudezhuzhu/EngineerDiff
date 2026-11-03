"""批量比较对话框：一个基准 + 多个候选文件，按“任意文件不同即标红”的或语义展示。"""

from __future__ import annotations

import copy
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QThread, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..batch import BatchCandidate, BatchError, BatchResult, run_batch
from ..file_loader import SUPPORTED_DIALOG_FILTER, classify
from ..models import AppSettings, CompareMode, Severity
from ..ocr.engine import OcrService
from . import theme


class _FileList(QListWidget):
    paths_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setStyleSheet(
            "QListWidget { background:#FFFFFF; border:1px solid #E0E0E0; border-radius:6px;"
            "padding:4px; } QListWidget::item { padding:5px 6px; border-radius:4px; }"
            "QListWidget::item:selected { background:#E5F1FB; color:#1B1B1B; }"
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class _BaselineCombo(QComboBox):
    """基准下拉框：支持从资源管理器把文件直接拖上来设为基准。"""

    paths_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setToolTip("选择基准文件；也可以把文件直接拖到此处设为基准")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if paths:
                self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class _BaselineDropZone(QFrame):
    """基准行：整行可接收资源管理器文件拖放（包括子控件上的拖放）。

    QComboBox 自身会消费 Drag/Drop，且其弹出列表是独立窗口；因此在整行框架
    安装递归事件过滤器，保证从“基准”文字、下拉框、提示或选择按钮上放下文件
    都能被识别。
    """

    paths_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("baselineZone")
        self.setAcceptDrops(True)
        self._app_filter_installed = False

    def install_drop_proxy(self) -> None:
        widgets = [self, *self.findChildren(QWidget)]
        for widget in widgets:
            try:
                widget.setAcceptDrops(True)
                widget.installEventFilter(self)
            except RuntimeError:
                continue
        # 某些平台/Qt 样式中，拖放事件目标可能是 QComboBox 的内部子控件；
        # 再挂一层应用级过滤器，按“鼠标是否落在基准行”兜底。
        try:
            if not self._app_filter_installed:
                from PySide6.QtWidgets import QApplication

                app = QApplication.instance()
                if app is not None:
                    app.installEventFilter(self)
                    self._app_filter_installed = True
        except Exception:
            pass

    def _inside_zone(self, obj, event) -> bool:
        if not self.isVisible():
            return False
        try:
            pos = event.position().toPoint()
        except Exception:
            try:
                pos = event.pos()
            except Exception:
                return False
        try:
            if isinstance(obj, QWidget):
                global_pos = obj.mapToGlobal(pos)
                return self.rect().contains(self.mapFromGlobal(global_pos))
        except Exception:
            pass
        return False

    @staticmethod
    def _url_paths(mime) -> list[str]:
        if mime is None or not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

    def _accept(self, event) -> bool:
        if self._url_paths(event.mimeData()):
            event.acceptProposedAction()
            return True
        return False

    def eventFilter(self, obj, event):  # noqa: N802
        etype = event.type()
        if etype in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if self._accept(event) and (obj is self or isinstance(obj, QWidget) and self._inside_zone(obj, event)):
                return True
        elif etype == QEvent.Type.Drop:
            paths = self._url_paths(event.mimeData())
            if paths and (obj is self or isinstance(obj, QWidget) and self._inside_zone(obj, event)):
                self.paths_dropped.emit(paths)
                event.acceptProposedAction()
                return True
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if not self._accept(event):
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if not self._accept(event):
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = self._url_paths(event.mimeData())
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class BatchWorker(QThread):
    progress = Signal(int, str)
    candidate_done = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, baseline: str, candidates: list[str], settings: AppSettings,
                 ocr: OcrService | None, parent=None):
        super().__init__(parent)
        self._baseline = baseline
        self._candidates = list(candidates)
        self._settings = copy.deepcopy(settings)
        self._ocr = ocr
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # noqa: D401
        try:
            result = run_batch(
                self._baseline,
                self._candidates,
                self._settings,
                progress=lambda p, m: self.progress.emit(p, m),
                cancel=self._cancel.is_set,
                ocr_service=self._ocr,
                on_candidate=lambda _i, cand: self.candidate_done.emit(cand),
            )
            if self._cancel.is_set():
                self.cancelled.emit()
            else:
                self.succeeded.emit(result)
        except Exception as exc:  # noqa: BLE001
            if self._cancel.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))


class BatchCompareDialog(QDialog):
    """批量差异表：基准为列表第一项；任意候选不同即标红。"""

    def __init__(self, parent=None, settings: AppSettings | None = None,
                 initial_left: str = "", initial_right: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("EngineerDiff - 批量比较")
        self.resize(1320, 820)
        self.setMinimumSize(980, 620)
        self._settings = copy.deepcopy(settings or AppSettings())
        self._worker: BatchWorker | None = None
        self._result: BatchResult | None = None
        self._ocr: OcrService | None = None
        self._build_ui()
        self.apply_theme()
        for path in (initial_left, initial_right):
            if path:
                self._add_paths([path])

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)

        self.lbl_page_title = QLabel("批量比较")
        self.lbl_page_title.setObjectName("pageTitle")
        root.addWidget(self.lbl_page_title)
        hint = QLabel(
            "默认第一个文件为基准；可把文件拖到“基准”框、点击右侧选择，"
            "或在全部文件中双击/选中后点“设为基准”。其余文件依次与基准比较。"
            "同一位置只要有任意一个文件不同，整行标红（或关系）。"
        )
        hint.setWordWrap(True)
        self.lbl_hint = hint
        root.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        self._splitter = splitter

        # 文件面板
        file_panel = QFrame()
        file_panel.setObjectName("filePanel")
        fp = QVBoxLayout(file_panel)
        fp.setContentsMargins(10, 8, 10, 8)
        fp.setSpacing(6)
        # 基准框与全部文件框使用同一种 ImportBox 视觉；只有高度不同。
        self.baseline_zone = _BaselineDropZone()
        self.baseline_zone.setObjectName("importBox")
        self.baseline_zone.setMinimumHeight(88)
        bz = QVBoxLayout(self.baseline_zone)
        bz.setContentsMargins(10, 7, 10, 6)
        bz.setSpacing(4)
        fph = QHBoxLayout()
        fph.setSpacing(6)
        self.lbl_baseline = QLabel("基准：")
        fph.addWidget(self.lbl_baseline, 1)
        self.btn_pick_baseline = QPushButton("选择")
        self.btn_pick_baseline.setObjectName("tool")
        self.btn_pick_baseline.setToolTip("手动选择基准文件；也可以把文件直接拖到这一框")
        fph.addWidget(self.btn_pick_baseline)
        self.baseline_zone.paths_dropped.connect(self._set_baseline_from_paths)
        # 隐藏的 combo 只用于维护当前基准路径；界面上显示为单行文件框。
        self.combo_baseline = _BaselineCombo(self.baseline_zone)
        self.combo_baseline.setVisible(False)
        self.combo_baseline.setMinimumWidth(220)
        self.baseline_list = _FileList(self.baseline_zone)
        self.baseline_list.setFixedHeight(36)
        self.baseline_list.setToolTip("拖动文件到基准框可设为基准；也可点击右侧“选择”")
        self.baseline_list.paths_dropped.connect(self._set_baseline_from_paths)
        bz.addLayout(fph)
        bz.addWidget(self.baseline_list, 1)
        self.baseline_zone.install_drop_proxy()
        fp.addWidget(self.baseline_zone)

        # 全部文件框
        self.all_zone = _BaselineDropZone()
        self.all_zone.setObjectName("importBox")
        self.all_zone.setMinimumHeight(150)
        az = QVBoxLayout(self.all_zone)
        az.setContentsMargins(10, 7, 10, 6)
        az.setSpacing(4)
        ah = QHBoxLayout()
        ah.setSpacing(6)
        self.lbl_all = QLabel("全部：")
        ah.addWidget(self.lbl_all)
        ah.addStretch(1)
        self.lbl_count = QLabel("共 0 个文件")
        ah.addWidget(self.lbl_count)
        ah.addStretch(1)
        self.btn_remove = QPushButton("移除选中")
        self.btn_remove.setObjectName("tool")
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("tool")
        self.btn_set_baseline = QPushButton("设为基准")
        self.btn_set_baseline.setObjectName("tool")
        self.btn_set_baseline.setToolTip("把全部文件中选中的文件设为基准")
        ah.addWidget(self.btn_remove)
        ah.addWidget(self.btn_clear)
        ah.addWidget(self.btn_set_baseline)
        self.btn_add = QPushButton("选择")
        self.btn_add.setObjectName("tool")
        self.btn_add.setToolTip("手动选择一个或多个文件加入全部文件")
        ah.addWidget(self.btn_add)
        self.all_zone.paths_dropped.connect(self._add_paths)
        self.file_list = _FileList(self.all_zone)
        self.file_list.setMaximumHeight(150)
        self.file_list.paths_dropped.connect(self._add_paths)
        az.addLayout(ah)
        az.addWidget(self.file_list, 1)
        self.all_zone.install_drop_proxy()
        fp.addWidget(self.all_zone, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_start = QPushButton("开始批量比较")
        self.btn_start.setObjectName("primary")
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("tool")
        self.btn_cancel.setVisible(False)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_cancel)
        fp.addLayout(btn_row)
        splitter.addWidget(file_panel)

        # 结果面板
        result_panel = QFrame()
        result_panel.setObjectName("batchResultPanel")
        rp = QVBoxLayout(result_panel)
        rp.setContentsMargins(10, 8, 10, 8)
        rp.setSpacing(6)
        result_head = QHBoxLayout()
        self.lbl_summary = QLabel("尚未开始比较")
        result_head.addWidget(self.lbl_summary, 1)
        self.cb_same = QCheckBox("显示一致项")
        self.cb_same.setChecked(True)
        self.cb_same.setToolTip("取消勾选后，与基准一致的候选值留空，只突出差异单元格")
        self.cb_same.toggled.connect(self._on_same_toggled)
        result_head.addWidget(self.cb_same)
        legend = QLabel("红 = 确定差异 · 橙 = 需人工复核")
        result_head.addWidget(legend)
        self.btn_copy = QPushButton("复制差异表")
        self.btn_copy.setObjectName("tool")
        self.btn_copy.setEnabled(False)
        result_head.addWidget(self.btn_copy)
        self.btn_image = QPushButton("原图对照")
        self.btn_image.setObjectName("tool")
        self.btn_image.setEnabled(False)
        self.btn_image.setVisible(False)
        self.btn_image.clicked.connect(self._open_image_view)
        result_head.addWidget(self.btn_image)
        rp.addLayout(result_head)

        self.table = QTableWidget(0, 2)
        self._groups: list[tuple[int, list[int], str]] = []
        self._row_models: dict[int, object] = {}
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setVisible(False)
        rp.addWidget(self.table, 1)
        self.lbl_result_empty = QLabel("导入 2 个及以上文件后点击“开始批量比较”，差异矩阵将显示在这里。")
        self.lbl_result_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_result_empty.setStyleSheet("color:#757575;")
        rp.addWidget(self.lbl_result_empty, 1)

        self.lbl_status = QLabel("")
        rp.addWidget(self.lbl_status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        rp.addWidget(self.progress)
        splitter.addWidget(result_panel)
        splitter.setSizes([210, 520])
        root.addWidget(splitter, 1)

        self.btn_close = QPushButton("关闭")
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(self.btn_close)
        root.addLayout(close_row)

        self.btn_add.clicked.connect(self._choose_files)
        self.btn_pick_baseline.clicked.connect(self._choose_baseline)
        self.btn_set_baseline.clicked.connect(self._set_baseline_from_selection)
        self.file_list.itemDoubleClicked.connect(self._set_baseline_from_item)
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear.clicked.connect(self.file_list.clear)
        self.btn_start.clicked.connect(self._start)
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_copy.clicked.connect(self._copy_table)
        self.btn_close.clicked.connect(self.reject)
        self.table.cellClicked.connect(self._toggle_group)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self.file_list.model().rowsInserted.connect(self._refresh_file_count)
        self.file_list.model().rowsRemoved.connect(self._refresh_file_count)
        self._refresh_file_count()

    # ------------------------------------------------------------------ integration
    def sync_files(self, left: str, right: str) -> None:
        """主窗口切到批量页时，自动带入当前 A/B；已有批量列表不覆盖。"""
        if self.file_list.count() <= 1:
            wanted = [p for p in (left, right) if p]
            if wanted:
                self._add_paths(wanted)

    def apply_theme(self) -> None:
        p = theme.palette()
        self.setStyleSheet(f"QDialog {{ background:{p.app_bg}; }}")
        self.lbl_page_title.setStyleSheet(
            f"font-size:17pt;font-weight:600;color:{p.text};"
        )
        self.lbl_hint.setStyleSheet(f"color:{p.text_secondary};")
        self.lbl_count.setStyleSheet(f"color:{p.text_secondary};")
        self.lbl_baseline.setStyleSheet(f"font-weight:600;color:{p.text};")
        self.combo_baseline.setStyleSheet("")
        self.lbl_summary.setStyleSheet(f"font-weight:600;color:{p.text};")
        self.lbl_status.setStyleSheet(f"color:{p.text_secondary};")
        self.lbl_result_empty.setStyleSheet(f"color:{p.text_secondary};")
        self.lbl_all.setStyleSheet(f"font-weight:600;color:{p.text};")
        box_style = (
            f"QFrame#importBox {{ background:{p.secondary_surface};"
            f"border:1px solid {p.divider}; border-radius:6px; }}"
        )
        self.baseline_zone.setStyleSheet(box_style)
        self.all_zone.setStyleSheet(box_style)
        self.table.setStyleSheet(
            f"QTableWidget {{ background:{p.card_bg}; border:1px solid {p.divider};"
            f"gridline-color:{p.divider}; color:{p.text}; }}"
            f"QTableWidget::item {{ padding:4px 6px; }}"
            f"QTableWidget::item:selected {{ background:{p.accent_soft}; color:{p.text}; }}"
        )
        list_style = (
            f"QListWidget {{ background:{p.card_bg}; border:none;"
            f"border-radius:0;padding:2px 4px;color:{p.text}; }}"
            f"QListWidget::item {{ padding:5px 6px; border-radius:4px; }}"
            f"QListWidget::item:hover {{ background:{p.hover_surface}; }}"
            f"QListWidget::item:selected {{ background:{p.accent_soft}; color:{p.text}; }}"
        )
        self.file_list.setStyleSheet(list_style)
        self.baseline_list.setStyleSheet(list_style)
        for frame in self.findChildren(QFrame):
            if frame.objectName() == "filePanel":
                frame.setStyleSheet(
                    f"QFrame#filePanel {{ background:transparent; border:none;"
                    f"border-bottom:1px solid {p.divider}; border-radius:0; }}"
                )
            elif frame.objectName() == "batchResultPanel":
                frame.setStyleSheet(
                    f"QFrame#batchResultPanel {{ background:{p.card_bg}; border:1px solid {p.divider};"
                    "border-radius:8px; }"
                )
        self.btn_close.setStyleSheet("")
        if self._result is not None:
            self._populate(self._result)

    # ------------------------------------------------------------------ files
    def _add_paths(self, paths: list[str]) -> None:
        existing = {
            self.file_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.file_list.count())
        }
        for path in paths:
            try:
                info = classify(path)
            except Exception:
                continue
            if not info.exists or info.path in existing:
                continue
            item = QListWidgetItem()
            label = f"{info.name}    {info.type_label} · {info.human_size}"
            item.setText(label)
            item.setData(Qt.ItemDataRole.UserRole, info.path)
            item.setData(Qt.ItemDataRole.UserRole + 1, label)
            item.setToolTip(info.path)
            self.file_list.addItem(item)
            existing.add(info.path)

    def _set_baseline_from_paths(self, paths: list[str]) -> None:
        """把拖到基准下拉框上的文件加入列表并设为基准。"""
        if not paths:
            return
        self._add_paths(paths)
        target = paths[0]
        try:
            target_resolved = Path(target).resolve()
        except Exception:
            target_resolved = Path(target)
        for i in range(self.combo_baseline.count()):
            data = self.combo_baseline.itemData(i)
            if not data:
                continue
            try:
                if Path(data).resolve() == target_resolved:
                    self.combo_baseline.setCurrentIndex(i)
                    break
            except Exception:
                continue
        self._refresh_file_count()
        self.lbl_status.setText("基准已切换；点击“重新比较”生成新的差异矩阵。")

    def _set_baseline_from_selection(self) -> None:
        items = self.file_list.selectedItems()
        if not items:
            self.lbl_status.setText("请先在全部文件列表中选择一个文件。")
            return
        path = items[0].data(Qt.ItemDataRole.UserRole)
        if path:
            self._set_baseline_from_paths([path])

    def _set_baseline_from_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._set_baseline_from_paths([path])

    def _choose_baseline(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择基准文件（会自动加入全部文件）", "", SUPPORTED_DIALOG_FILTER
        )
        if path:
            self._set_baseline_from_paths([path])

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "添加批量比较文件（第一个默认为基准）", "", SUPPORTED_DIALOG_FILTER
        )
        if paths:
            self._add_paths(paths)

    def _remove_selected(self) -> None:
        rows = sorted({self.file_list.row(item) for item in self.file_list.selectedItems()}, reverse=True)
        for row in rows:
            self.file_list.takeItem(row)

    def _paths(self) -> list[str]:
        return [
            self.file_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.file_list.count())
        ]

    def _refresh_file_count(self, *_args) -> None:
        paths = self._paths()
        self.lbl_count.setText(f"共 {len(paths)} 个文件")
        current = self.combo_baseline.currentData()
        self.combo_baseline.blockSignals(True)
        self.combo_baseline.clear()
        for path in paths:
            self.combo_baseline.addItem(Path(path).name, path)
        if paths:
            idx = self.combo_baseline.findData(current)
            self.combo_baseline.setCurrentIndex(idx if idx >= 0 else 0)
            self.combo_baseline.setEnabled(True)
        else:
            self.combo_baseline.setEnabled(False)
        self.combo_baseline.blockSignals(False)
        baseline = self.combo_baseline.currentData() or (paths[0] if paths else "")
        self.baseline_list.clear()
        p = theme.palette()
        if baseline:
            try:
                info = classify(baseline)
                label = f"{info.name}    {info.type_label} · {info.human_size}"
            except Exception:
                label = Path(baseline).name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, baseline)
            item.setToolTip(baseline)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.baseline_list.addItem(item)
        else:
            item = QListWidgetItem("拖入或选择基准文件")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setForeground(QBrush(QColor(p.text_secondary)))
            self.baseline_list.addItem(item)

    # ------------------------------------------------------------------ run
    def _start(self) -> None:
        paths = self._paths()
        if len(paths) < 2:
            QMessageBox.information(self, "批量比较", "请至少添加 2 个文件（第一个为基准）。")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        baseline = self.combo_baseline.currentData() or paths[0]
        candidates = [p for p in paths if p != baseline]
        if not candidates:
            QMessageBox.information(self, "批量比较", "请至少保留一个待比较文件。")
            return
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            base_label = item.data(Qt.ItemDataRole.UserRole + 1) or item.text()
            item.setText(base_label)
        self.table.clearContents()
        self.table.setRowCount(0)
        self.table.setVisible(False)
        self.lbl_result_empty.setVisible(True)
        self.lbl_summary.setText("正在比较…")
        self.lbl_status.setText("")
        self._set_running(True)
        # 图片批量比较才需要 OCR 服务；复用默认缓存目录，模型进程内共享。
        ocr = None
        try:
            if all(classify(p).kind.value == "image" for p in paths):
                ocr = OcrService()
        except Exception:
            ocr = None
        self._ocr = ocr
        self._worker = BatchWorker(baseline, candidates, self._settings, ocr, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.candidate_done.connect(self._on_candidate_done)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.lbl_status.setText("正在取消…")
            self.btn_cancel.setEnabled(False)

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        self.lbl_status.setText(message)

    def _on_candidate_done(self, cand: BatchCandidate) -> None:
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) != cand.path:
                continue
            base_label = item.data(Qt.ItemDataRole.UserRole + 1) or cand.name
            if cand.error:
                item.setText(f"{base_label}    ✗ 失败")
                item.setToolTip(f"{cand.path}\n{cand.error}")
            else:
                item.setText(f"{base_label}    ✓ {cand.diff_count} 处差异")
                item.setToolTip(cand.path)
            break

    def _on_succeeded(self, result: BatchResult) -> None:
        self._result = result
        self._populate(result)
        self._set_running(False)
        self.progress.setVisible(False)
        self.lbl_summary.setText(
            f"共 {result.file_count} 个文件 · {result.different_count} 处位置存在差异"
            f"（任意文件不同即标红）· {result.elapsed:.1f}s"
        )
        if result.warnings:
            self.lbl_status.setText("；".join(result.warnings[:3]))
            self.lbl_status.setToolTip("\n".join(result.warnings))
        else:
            self.lbl_status.setText("完成")
        self.btn_copy.setEnabled(bool(result.rows))
        # 导入完成后收缩文件区，把主要空间让给差异矩阵
        self.file_list.setMaximumHeight(112)
        try:
            self._splitter.setSizes([142, 620])
        except Exception:
            pass
        is_image = result.mode.value == "image" and any(c.result is not None for c in result.candidates)
        self.btn_image.setVisible(is_image)
        self.btn_image.setEnabled(is_image)

    def _on_failed(self, message: str) -> None:
        self._set_running(False)
        self.progress.setVisible(False)
        self.lbl_summary.setText("批量比较失败")
        QMessageBox.critical(self, "批量比较失败", message)

    def _on_cancelled(self) -> None:
        self._set_running(False)
        self.progress.setVisible(False)
        self.lbl_summary.setText("批量比较已取消")

    def _on_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    def _set_running(self, running: bool) -> None:
        self.progress.setVisible(running)
        self.btn_start.setEnabled(not running)
        self.btn_add.setEnabled(not running)
        self.btn_remove.setEnabled(not running)
        self.btn_clear.setEnabled(not running)
        self.file_list.setEnabled(not running)
        self.baseline_list.setEnabled(not running)
        self.combo_baseline.setEnabled(not running and self.file_list.count() > 0)
        self.btn_pick_baseline.setEnabled(not running)
        self.baseline_zone.setEnabled(not running)
        self.all_zone.setEnabled(not running)
        self.btn_cancel.setVisible(running)
        self.btn_cancel.setEnabled(running)

    # ------------------------------------------------------------------ table
    @staticmethod
    def _short(text: str, limit: int = 220) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ⏎ ")
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _populate(self, result: BatchResult) -> None:
        self.table.setVisible(True)
        self.lbl_result_empty.setVisible(False)
        self.table.clearContents()
        self.table.clearSpans()
        candidates = result.candidates
        n = len(candidates)
        self.table.setColumnCount(2 + n)
        headers = ["位置", f"基准：{Path(result.baseline).name}"]
        for cand in candidates:
            suffix = f"（{cand.diff_count} 处）" if not cand.error else "（失败）"
            headers.append(f"{cand.name}{suffix}")
        self.table.setHorizontalHeaderLabels(headers)
        for col, header in enumerate(headers):
            item = self.table.horizontalHeaderItem(col)
            if item is not None:
                item.setToolTip("差异所在位置" if col == 0 else (
                    result.baseline if col == 1 else candidates[col - 2].path
                ))

        specs: list[tuple[str, object, int]] = []
        if result.mode.value == "excel":
            by_sheet: dict[str, list] = {}
            for row in result.rows:
                by_sheet.setdefault(row.sheet or "（无 Sheet）", []).append(row)
            for sheet, rows_in_sheet in by_sheet.items():
                specs.append(("group", sheet, len(rows_in_sheet)))
                specs.extend(("row", row, 0) for row in rows_in_sheet)
        else:
            specs.extend(("row", row, 0) for row in result.rows)

        self.table.setRowCount(len(specs))
        self._groups = []
        self._row_models = {}
        p = theme.palette()
        group_bg = QColor(p.secondary_surface)
        strong = QColor(p.diff_red_bg)
        warning = QColor(p.warning_bg)
        soft_red = QColor(p.diff_red_bg)
        soft_red.setAlpha(90)
        soft_warn = QColor(p.warning_bg)
        soft_warn.setAlpha(90)
        for r, (kind, payload, count) in enumerate(specs):
            if kind == "group":
                sheet = str(payload)
                item = QTableWidgetItem(f"  ▾  工作表「{sheet}」 · {count} 处差异")
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setBackground(group_bg)
                item.setForeground(QBrush(QColor(p.text)))
                self.table.setItem(r, 0, item)
                self.table.setSpan(r, 0, 1, self.table.columnCount())
                self.table.setRowHeight(r, 30)
                self._groups.append((r, list(range(r + 1, r + 1 + count)), sheet, count))
                continue
            row = payload
            self._row_models[r] = row
            uncertain = row.severity == Severity.UNCERTAIN
            cell_bg = warning if uncertain else strong
            row_bg = soft_warn if uncertain else soft_red
            base = QTableWidgetItem(self._short(row.location))
            base.setToolTip(row.location)
            base.setBackground(row_bg)
            self.table.setItem(r, 0, base)
            b = QTableWidgetItem(self._short(row.baseline))
            b.setToolTip(row.baseline)
            b.setBackground(cell_bg)
            self.table.setItem(r, 1, b)
            for c in range(n):
                value = row.values[c] if c < len(row.values) else "—"
                it = QTableWidgetItem(self._short(value))
                it.setToolTip(value)
                if row.differs[c]:
                    it.setBackground(cell_bg)
                    it.setForeground(QBrush(QColor(p.diff_red_text if not uncertain else p.warning_text)))
                elif not self.cb_same.isChecked():
                    it.setText("")
                    it.setToolTip("与基准一致（已隐藏）")
                self.table.setItem(r, 2 + c, it)
            self.table.setRowHeight(r, 26)
        self._size_columns()

    def _on_same_toggled(self, _checked: bool) -> None:
        if self._result is not None:
            self._populate(self._result)

    def _on_row_double_clicked(self, row: int, _column: int) -> None:
        if self._result is None or self._result.mode.value != "image":
            return
        model = self._row_models.get(row)
        if model is None or model.image_diff is None:
            return
        try:
            from .batch_image_dialog import BatchImageDialog

            dlg = BatchImageDialog(
                self._result, self, max(0, model.image_candidate), model.image_diff
            )
            dlg.exec()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "原图对照", f"无法定位原图：{exc}")

    def _toggle_group(self, row: int, _column: int = 0) -> None:
        for group_row, children, sheet, count in self._groups:
            if group_row != row:
                continue
            hidden = not self.table.isRowHidden(children[0]) if children else True
            for child in children:
                self.table.setRowHidden(child, hidden)
            item = self.table.item(group_row, 0)
            if item is not None:
                item.setText(f"  {'▸' if hidden else '▾'}  工作表「{sheet}」 · {count} 处差异")
            return

    def _size_columns(self) -> None:
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        available = max(600, self.table.viewport().width())
        n = max(1, self.table.columnCount() - 1)
        location_width = 150
        candidate_width = max(190, int((available - location_width - 20) / n))
        self.table.setColumnWidth(0, location_width)
        for c in range(1, self.table.columnCount()):
            self.table.setColumnWidth(c, candidate_width)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.table.columnCount() > 1:
            self._size_columns()

    def _open_image_view(self) -> None:
        if self._result is None:
            return
        try:
            from .batch_image_dialog import BatchImageDialog

            BatchImageDialog(self._result, self).exec()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "原图对照", f"无法打开原图对照：{exc}")

    def _copy_table(self) -> None:
        if self.table.rowCount() == 0:
            return
        # 表头 + 每行 TSV
        header = "\t".join(
            (self.table.horizontalHeaderItem(c).text() if self.table.horizontalHeaderItem(c) else "")
            for c in range(self.table.columnCount())
        )
        out = [header]
        for r in range(self.table.rowCount()):
            out.append("\t".join(
                (self.table.item(r, c).text() if self.table.item(r, c) else "")
                for c in range(self.table.columnCount())
            ))
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText("\n".join(out))
        self.lbl_status.setText("差异表已复制到剪贴板（TSV）")

    # ------------------------------------------------------------------ close
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(8000)
            if self._worker.isRunning():
                self._worker.wait()
        super().closeEvent(event)
