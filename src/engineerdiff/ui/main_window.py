"""EngineerDiff 主窗口。"""

from __future__ import annotations

import copy
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QIcon, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from .. import version
from ..compare import ComparisonCancelled
from ..file_loader import (
    IMAGE_DIALOG_FILTER,
    SPREADSHEET_DIALOG_FILTER,
    SUPPORTED_DIALOG_FILTER,
    TEXT_DIALOG_FILTER,
    FileKind,
    IncompatibleFilesError,
    classify,
    determine_mode,
    mode_label,
)
from ..models import AppSettings, CompareMode, ComparisonResult, DiffItem, SheetStatus, TextCompareOptions
from ..textdiff.engine import compare_text_files  # noqa: F401  (保持引擎可被此处测试替换)
from . import theme
from .panes import SidePane
from .settings_dialog import SettingsDialog
from .text_view import TextView
from .workers import CompareWorker


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{version.APP_NAME} by {version.AUTHOR}")
        self.resize(1440, 900)
        self.setMinimumSize(1050, 680)
        self._settings = AppSettings()
        self._qsettings = QSettings(version.ORG_NAME, version.APP_NAME)
        self._load_settings()

        self._left_path = ""
        self._right_path = ""
        self._last_focus = "right"
        self._worker: CompareWorker | None = None
        self._pending_drop: tuple[str, str, bool] | None = None
        self._result: ComparisonResult | None = None
        self._mode: CompareMode | None = None
        self._view = "text"
        self._diffs: list[DiffItem] = []
        self._idx = -1
        self._busy = False
        self._sync_guard = False
        self._cursor_overridden = False
        self._ocr_service = None
        self._text_views: dict[str, TextView] = {}
        self._sheet_sides: dict[str, object] = {}
        self._image_panes: dict[str, object] = {}
        self._preview_panes: dict[str, object] = {}
        self._image_sync_ready = False
        self._prefetching: set[str] = set()
        self._batch_page = None
        self.batch_page = None
        self._single_status: tuple[str, str, str, str] | None = None
        self._csv_stacks: dict[str, QStackedWidget] = {}
        self._temp_dir = tempfile.mkdtemp(prefix="engineerdiff-paste-")

        self._build_ui()
        self._restore_geometry()
        self._apply_mode_controls()
        theme.register_listener(self._on_theme_changed)
        self._on_theme_changed(theme.palette())
        try:
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
        except Exception:
            pass
        self._update_ui_state()

    # ================================================================== UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 4)
        root.setSpacing(8)

        # ---------- Fluent 顶部导航 ----------
        header = QWidget()
        header.setObjectName("appHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(8)
        # 原生 Windows 标题栏已显示“EngineerDiff by jzh”，窗口内不再重复品牌行；
        # 顶部只保留工作模式导航与设置，减少视觉噪声。
        self.nav = QTabBar()
        self.nav.setObjectName("modeNav")
        self.nav.addTab("单文件比较")
        self.nav.addTab("批量比较")
        self.nav.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        hl.addWidget(self.nav)
        hl.addStretch(1)
        self.btn_settings = self._tool_button("设置", "比较设置")
        hl.addWidget(self.btn_settings)
        root.addWidget(header)

        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)

        # 单文件比较页
        self.single_page = QWidget()
        single_layout = QVBoxLayout(self.single_page)
        single_layout.setContentsMargins(0, 0, 0, 0)
        single_layout.setSpacing(6)
        self.pages.addWidget(self.single_page)

        # ---------- 顶部文件区 ----------
        self.file_panel = QFrame()
        self.file_panel.setObjectName("filePanel")
        self.file_panel.setStyleSheet(
            "QFrame#filePanel { background:#FFFFFF; border:1px solid #E0E0E0; border-radius:10px; }"
        )
        fp = QVBoxLayout(self.file_panel)
        fp.setContentsMargins(8, 7, 8, 7)
        fp.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.left_edit = self._make_path_edit("选择基准文件 A（左）")
        self.right_edit = self._make_path_edit("选择待比较文件 B（右）")
        self.btn_pick_left = self._tool_button("选择 A…", "选择基准文件 A")
        self.btn_pick_right = self._tool_button("选择 B…", "选择待比较文件 B")
        self.btn_reload_left = self._tool_button("↻", "重新加载 A 并重新比较")
        self.btn_reload_right = self._tool_button("↻", "重新加载 B 并重新比较")
        self.btn_swap = self._tool_button("⇄", "交换左右文件")
        self.btn_compare = QPushButton("开始比较")
        self.btn_compare.setObjectName("primary")
        self.btn_compare.setMinimumWidth(110)
        self.lbl_a = self._field_label("A", "#0067C0")
        row1.addWidget(self.lbl_a)
        row1.addWidget(self.left_edit, 1)
        row1.addWidget(self.btn_pick_left)
        row1.addWidget(self.btn_reload_left)
        row1.addSpacing(4)
        row1.addWidget(self.btn_swap)
        row1.addSpacing(4)
        self.lbl_b = self._field_label("B", "#8A8A8A")
        row1.addWidget(self.lbl_b)
        row1.addWidget(self.right_edit, 1)
        row1.addWidget(self.btn_pick_right)
        row1.addWidget(self.btn_reload_right)
        row1.addSpacing(8)
        row1.addWidget(self.btn_compare)
        fp.addLayout(row1)

        # ---------- 差异工具栏 ----------
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.btn_prev = self._tool_button("◀ 上一处", "上一处差异（F7）")
        self.btn_next = self._tool_button("下一处 ▶", "下一处差异（F8）")
        self.lbl_index = QLabel("0 / 0")
        self.lbl_index.setMinimumWidth(64)
        self.lbl_index.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_index.setStyleSheet("font-weight:600;color:#1B1B1B;")
        self.cb_sync = QCheckBox("同步滚动")
        self.cb_sync.setChecked(self._settings.sync_scroll)
        self.cb_only = QCheckBox("仅显示差异")
        self.cb_only.setChecked(self._settings.only_diffs)
        self.cb_only.setToolTip("文本/表格视图只显示差异及其相邻上下文")
        self.cb_sheet_sync = QCheckBox("同步 Sheet")
        self.cb_sheet_sync.setChecked(self._settings.sync_sheets)
        self.cb_sheet_sync.setToolTip("切换 Excel 工作表时，两侧自动切换到同名工作表（可独立关闭）")
        self.cb_sheet_sync.setVisible(False)
        self.sheet_tools = QWidget()
        st = QHBoxLayout(self.sheet_tools)
        st.setContentsMargins(0, 0, 0, 0)
        st.setSpacing(4)
        self.btn_sheet_zoom_out = self._tool_button("−", "缩小表格")
        self.btn_sheet_zoom_in = self._tool_button("+", "放大表格")
        self.btn_sheet_zoom_reset = self._tool_button("100%", "表格缩放复位")
        self.btn_sheet_zoom_reset.setMinimumWidth(52)
        st.addWidget(QLabel("表格缩放"))
        st.addWidget(self.btn_sheet_zoom_out)
        st.addWidget(self.btn_sheet_zoom_reset)
        st.addWidget(self.btn_sheet_zoom_in)
        self.sheet_tools.setVisible(False)
        self._sheet_zoom = 1.0
        self._sep1 = self._vsep()
        self.combo_view = QComboBox()
        self.combo_view.addItem("表格视图", "table")
        self.combo_view.addItem("文本视图", "text")
        self.combo_view.setToolTip("CSV 支持原始文本与表格两种视图")
        self.combo_view.setVisible(False)

        row2.addWidget(self.btn_prev)
        row2.addWidget(self.btn_next)
        row2.addWidget(self.lbl_index)
        row2.addWidget(self._sep1)
        row2.addWidget(self.cb_sync)
        row2.addWidget(self.cb_only)
        row2.addWidget(self.combo_view)
        row2.addWidget(self.cb_sheet_sync)
        row2.addWidget(self.sheet_tools)
        row2.addStretch(1)

        # 图片控件组
        self.image_tools = QWidget()
        it = QHBoxLayout(self.image_tools)
        it.setContentsMargins(0, 0, 0, 0)
        it.setSpacing(5)
        self.btn_fit = self._tool_button("适应窗口", "两侧图片适应窗口；鼠标滚轮直接缩放，Shift+滚轮横向平移")
        self.btn_actual = self._tool_button("原始尺寸", "两侧图片按 100% 显示（滚轮可缩放）")
        self.btn_zoom_out = self._tool_button("−", "缩小图片（也可直接滚动鼠标滚轮）")
        self.btn_zoom_in = self._tool_button("+", "放大图片（也可直接滚动鼠标滚轮）")
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setMinimumWidth(72)
        self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_zoom.setStyleSheet("color:#616161;")
        self.cb_boxes = QCheckBox("OCR 文字框")
        self.cb_boxes.setToolTip("显示全部 OCR 文字框与置信度，便于人工核对")
        self.btn_ocr_fix = self._tool_button("OCR 校对…", "查看/修正 OCR 结果，或手动指定文字对应关系")
        it.addWidget(self.btn_fit)
        it.addWidget(self.btn_actual)
        it.addWidget(self.btn_zoom_out)
        it.addWidget(self.btn_zoom_in)
        it.addWidget(self.lbl_zoom)
        it.addWidget(self.cb_boxes)
        it.addWidget(self.btn_ocr_fix)
        self.image_tools.setVisible(False)
        row2.addWidget(self.image_tools)
        fp.addLayout(row2)
        single_layout.addWidget(self.file_panel)

        # ---------- 警告条 ----------
        self.warning_bar = QFrame()
        self.warning_bar.setStyleSheet(
            "QFrame { background:#FFF4CE; border:1px solid #F2D58A; border-radius:6px; }"
            "QLabel { color:#5C4300; border:none; background:transparent; }"
            "QPushButton { background:transparent;border:none;color:#005A9E;text-decoration:underline; }"
        )
        wb = QHBoxLayout(self.warning_bar)
        wb.setContentsMargins(10, 4, 8, 4)
        self.lbl_warning = QLabel("")
        self.btn_warning = QPushButton("查看详情")
        self.btn_warning.setStyleSheet("")
        wb.addWidget(QLabel("⚠"))
        wb.addWidget(self.lbl_warning, 1)
        wb.addWidget(self.btn_warning)
        self.warning_bar.setVisible(False)
        single_layout.addWidget(self.warning_bar)

        # ---------- 左右内容 ----------
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(4)
        self.left_pane = SidePane("left")
        self.right_pane = SidePane("right")
        self.left_pane.setMinimumWidth(380)
        self.right_pane.setMinimumWidth(380)
        self.splitter.addWidget(self.left_pane)
        self.splitter.addWidget(self.right_pane)
        self.splitter.setSizes([700, 700])
        single_layout.addWidget(self.splitter, 1)

        # 批量比较页（复用现有批量界面，作为主窗口页面而非独立弹窗）
        try:
            from .batch_dialog import BatchCompareDialog

            self.batch_page = BatchCompareDialog(self, self._settings)
            self.batch_page.setWindowFlags(Qt.Widget)
            self.batch_page.btn_close.hide()
            # 批量页复用主窗口导航，隐藏页内重复的“批量比较”大标题
            self.batch_page.lbl_page_title.hide()
            self.batch_page.setMinimumSize(0, 0)
            self.pages.addWidget(self.batch_page)
        except Exception:
            self.batch_page = None

        # ---------- 状态栏 ----------
        status = QStatusBar()
        status.setSizeGripEnabled(True)
        self.setStatusBar(status)
        self.lbl_type = QLabel("未选择文件")
        self.lbl_type.setMinimumWidth(90)
        self.lbl_state = QLabel("就绪：选择左右文件后点击“开始比较”")
        self.lbl_state.setStyleSheet("color:#424242;")
        self.lbl_state.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lbl_state.setMinimumWidth(0)
        self.lbl_state.setToolTip("比较状态/当前差异")
        self.lbl_diff = QLabel("差异 0 处")
        self.lbl_diff.setStyleSheet("font-weight:600;color:#424242;")
        self.lbl_ocr = QLabel("")
        self.lbl_ocr.setMaximumWidth(220)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.btn_cancel_worker = QPushButton("取消")
        self.btn_cancel_worker.setObjectName("tool")
        self.btn_cancel_worker.setVisible(False)
        self.lbl_warn_count = QLabel("")
        self.lbl_warn_count.setStyleSheet("color:#9D5D00;")
        status.addWidget(self.lbl_type)
        status.addWidget(self.lbl_state, 1)
        status.addPermanentWidget(self.progress)
        status.addPermanentWidget(self.btn_cancel_worker)
        status.addPermanentWidget(self.lbl_warn_count)
        status.addPermanentWidget(self.lbl_ocr)
        status.addPermanentWidget(self.lbl_diff)

        # ---------- 信号 ----------
        self.btn_pick_left.clicked.connect(lambda: self._choose_file("left"))
        self.btn_pick_right.clicked.connect(lambda: self._choose_file("right"))
        self.btn_reload_left.clicked.connect(lambda: self._reload_side("left"))
        self.btn_reload_right.clicked.connect(lambda: self._reload_side("right"))
        self.btn_swap.clicked.connect(self._swap)
        self.btn_compare.clicked.connect(self._start_compare)
        self.btn_settings.clicked.connect(self._open_settings)
        self.nav.currentChanged.connect(self._switch_page)
        self.btn_prev.clicked.connect(lambda: self._step_diff(-1))
        self.btn_next.clicked.connect(lambda: self._step_diff(1))
        self.cb_sync.toggled.connect(self._on_sync_toggled)
        self.cb_only.toggled.connect(self._on_only_diffs_toggled)
        self.cb_sheet_sync.toggled.connect(self._on_sheet_sync_toggled)
        self.combo_view.currentIndexChanged.connect(self._on_view_changed)
        self.btn_fit.clicked.connect(lambda: self._image_action("fit"))
        self.btn_actual.clicked.connect(lambda: self._image_action("actual"))
        self.btn_zoom_in.clicked.connect(lambda: self._image_action("zoom_in"))
        self.btn_zoom_out.clicked.connect(lambda: self._image_action("zoom_out"))
        self.cb_boxes.toggled.connect(self._on_show_boxes)
        self.btn_ocr_fix.clicked.connect(self._open_ocr_correction)
        self.btn_sheet_zoom_in.clicked.connect(lambda: self._sheet_zoom_action(1))
        self.btn_sheet_zoom_out.clicked.connect(lambda: self._sheet_zoom_action(-1))
        self.btn_sheet_zoom_reset.clicked.connect(lambda: self._sheet_zoom_action(0))
        self.btn_warning.clicked.connect(self._show_warnings)
        self.btn_cancel_worker.clicked.connect(self._cancel_worker)
        self.left_pane.file_dropped.connect(lambda p: self._set_file("left", p, auto_compare=True))
        self.right_pane.file_dropped.connect(lambda p: self._set_file("right", p, auto_compare=True))
        self.left_pane.clicked.connect(lambda _s: self._set_focus_side("left"))
        self.right_pane.clicked.connect(lambda _s: self._set_focus_side("right"))

        QShortcut(QKeySequence("F7"), self, activated=lambda: self._step_diff(-1))
        QShortcut(QKeySequence("F8"), self, activated=lambda: self._step_diff(1))
        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self._paste_clipboard)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._start_compare)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=lambda: self._choose_file("left"))
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, activated=lambda: self._choose_file("right"))

    def _make_path_edit(self, placeholder: str) -> QLineEdit:
        e = QLineEdit()
        e.setReadOnly(True)
        e.setPlaceholderText(placeholder)
        e.setClearButtonEnabled(False)
        e.setMinimumHeight(28)
        return e

    def _field_label(self, text: str, color: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedSize(20, 20)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"background:{color};color:white;border-radius:4px;font-weight:600;font-size:9pt;"
        )
        return lbl

    def _tool_button(self, text: str, tooltip: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("tool")
        if tooltip:
            b.setToolTip(tooltip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    @staticmethod
    def _vsep() -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.VLine)
        f.setStyleSheet("color:#E0E0E0;")
        f.setFixedHeight(18)
        return f

    # ================================================================== theme
    def _on_system_scheme_changed(self, *_args) -> None:
        if self._settings.theme == "system":
            theme.apply_theme("system", QApplication.instance())

    def _on_theme_changed(self, palette=None) -> None:
        p = palette or theme.palette()
        from . import grid_view, image_view, spreadsheet_side

        try:
            grid_view.refresh_palette()
            spreadsheet_side.refresh_palette()
            image_view.refresh_palette()
        except Exception:
            pass
        try:
            self.left_pane.apply_theme(p)
            self.right_pane.apply_theme(p)
        except Exception:
            pass
        self.file_panel.setStyleSheet(
            f"QFrame#filePanel {{ background:transparent; border:none;"
            f"border-bottom:1px solid {p.divider}; border-radius:0; }}"
        )
        self.warning_bar.setStyleSheet(
            f"QFrame {{ background:{p.warning_bg}; border:1px solid {p.warning}; border-radius:6px; }}"
            f"QLabel {{ color:{p.warning_text}; border:none; background:transparent; }}"
            f"QPushButton {{ background:transparent;border:none;color:{p.accent};text-decoration:underline; }}"
        )
        for label in (self.lbl_index, self.lbl_zoom, self.lbl_diff, self.lbl_state):
            label.setStyleSheet(label.styleSheet())
        self.lbl_state.setStyleSheet(f"color:{p.text_secondary};")
        if self.lbl_diff.text().startswith("差异"):
            self.lbl_diff.setStyleSheet(
                f"font-weight:600;color:{p.success if self.lbl_diff.text() == '差异 0 处' else p.diff_red};"
            )
        else:
            self.lbl_diff.setStyleSheet(f"font-weight:600;color:{p.text_secondary};")
        self.lbl_zoom.setStyleSheet(f"color:{p.text_secondary};")
        self.lbl_index.setStyleSheet(f"font-weight:600;color:{p.text};")
        self.lbl_warn_count.setStyleSheet(f"color:{p.warning_text};")
        self.lbl_a.setStyleSheet(
            f"background:{p.accent};color:white;border-radius:4px;font-weight:600;font-size:9pt;"
        )
        self.lbl_b.setStyleSheet(
            f"background:{p.text_secondary};color:white;border-radius:4px;font-weight:600;font-size:9pt;"
        )
        for tv in self._text_views.values():
            tv.viewport().update()
        for ss in self._sheet_sides.values():
            ss.tab_bar().update()
            ss.grid().viewport().update()
        for ip in self._preview_panes.values():
            try: ip.apply_theme()
            except Exception: pass
        for ip in self._image_panes.values():
            try: ip.apply_theme()
            except Exception: pass
        self.update()
        theme.apply_dark_title_bar(self)
        if self.batch_page is not None:
            try: self.batch_page.apply_theme()
            except Exception: pass

    # ================================================================== settings
    def _load_settings(self) -> None:
        q = self._qsettings
        s = self._settings
        s.text.ignore_line_endings = q.value("text/ignore_line_endings", False, type=bool)
        s.text.ignore_outer_space = q.value("text/ignore_outer_space", False, type=bool)
        s.text.ignore_all_space = q.value("text/ignore_all_space", False, type=bool)
        s.text.ignore_case = q.value("text/ignore_case", False, type=bool)
        s.spreadsheet.formula_mode = q.value("sheet/formula_mode", "formula", type=str)
        try:
            s.spreadsheet.numeric_tolerance = float(q.value("sheet/tolerance", 0.0))
        except (TypeError, ValueError):
            s.spreadsheet.numeric_tolerance = 0.0
        s.spreadsheet.smart_rows = q.value("sheet/smart_rows", False, type=bool)
        try:
            s.image.confidence_threshold = float(q.value("image/confidence", 0.60))
        except (TypeError, ValueError):
            s.image.confidence_threshold = 0.60
        s.image.show_boxes = q.value("image/show_boxes", False, type=bool)
        s.sync_sheets = q.value("general/sync_sheets", True, type=bool)
        s.sync_scroll = q.value("general/sync_scroll", True, type=bool)
        s.only_diffs = q.value("general/only_diffs", False, type=bool)
        s.ocr_cache = q.value("general/ocr_cache", True, type=bool)
        s.theme = str(q.value("general/theme", "system"))
        if s.theme not in ("system", "light", "dark"):
            s.theme = "system"

    def _save_settings(self) -> None:
        q = self._qsettings
        s = self._settings
        q.setValue("text/ignore_line_endings", s.text.ignore_line_endings)
        q.setValue("text/ignore_outer_space", s.text.ignore_outer_space)
        q.setValue("text/ignore_all_space", s.text.ignore_all_space)
        q.setValue("text/ignore_case", s.text.ignore_case)
        q.setValue("sheet/formula_mode", s.spreadsheet.formula_mode)
        q.setValue("sheet/tolerance", s.spreadsheet.numeric_tolerance)
        q.setValue("sheet/smart_rows", s.spreadsheet.smart_rows)
        q.setValue("image/confidence", s.image.confidence_threshold)
        q.setValue("image/show_boxes", s.image.show_boxes)
        q.setValue("general/sync_sheets", s.sync_sheets)
        q.setValue("general/sync_scroll", s.sync_scroll)
        q.setValue("general/only_diffs", s.only_diffs)
        q.setValue("general/ocr_cache", s.ocr_cache)
        q.setValue("general/theme", s.theme)

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(max(0, min(1, index)))
        if index == 1 and self.batch_page is not None:
            if self._single_status is None:
                self._single_status = (
                    self.lbl_type.text(), self.lbl_state.text(),
                    self.lbl_diff.text(), self.lbl_ocr.text(),
                )
            self.batch_page.sync_files(self._left_path, self._right_path)
            self.lbl_type.setText("批量比较")
            self.lbl_state.setText("批量矩阵：第一个文件为基准 A")
            self.lbl_diff.setText("")
            self.lbl_ocr.setText("")
            self.lbl_warn_count.setText("")
            self.progress.setVisible(False)
            self.btn_cancel_worker.setVisible(False)
        elif index == 0:
            if self._single_status is not None:
                self.lbl_type.setText(self._single_status[0])
                self.lbl_state.setText(self._single_status[1])
                self.lbl_diff.setText(self._single_status[2])
                self.lbl_ocr.setText(self._single_status[3])
                self._single_status = None
            self._update_ui_state()

    def _open_batch_dialog(self) -> None:
        self.nav.setCurrentIndex(1)

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._settings, self)
        dlg.btn_clear_cache.clicked.connect(self._clear_ocr_cache)
        if dlg.exec():
            self._settings = dlg.result_settings()
            self.cb_sync.setChecked(self._settings.sync_scroll)
            self.cb_only.setChecked(self._settings.only_diffs)
            self.cb_sheet_sync.setChecked(self._settings.sync_sheets)
            self._save_settings()
            theme.apply_theme(self._settings.theme, QApplication.instance())
            self.cb_boxes.blockSignals(True)
            self.cb_boxes.setChecked(self._settings.image.show_boxes)
            self.cb_boxes.blockSignals(False)
            for pane in self._image_panes.values():
                pane.set_show_boxes(self._settings.image.show_boxes)
            self.lbl_state.setText("设置已保存，将在下一次比较时生效。")
            if self._result is not None:
                self.lbl_state.setText("设置已保存；点击“重新比较”应用新设置。")

    def _clear_ocr_cache(self) -> None:
        try:
            svc = self._get_ocr_service()
            svc.clear_cache()
            QMessageBox.information(self, "OCR 缓存", "OCR 缓存已清除。")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "OCR 缓存", f"清除缓存失败：{exc}")

    def _restore_geometry(self) -> None:
        geo = self._qsettings.value("window/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = self._qsettings.value("window/splitter")
        if state is not None:
            self.splitter.restoreState(state)

    # ================================================================== file input
    def _filter_for_other(self, side: str) -> str:
        other = self._right_path if side == "left" else self._left_path
        if other:
            try:
                kind = classify(other).kind
                if kind in (FileKind.XLS, FileKind.XLSX):
                    return SPREADSHEET_DIALOG_FILTER
                if kind == FileKind.IMAGE:
                    return IMAGE_DIALOG_FILTER
                return TEXT_DIALOG_FILTER
            except IncompatibleFilesError:
                pass
        return SUPPORTED_DIALOG_FILTER

    def _choose_file(self, side: str) -> None:
        start_dir = str(Path(self._left_path or self._right_path or Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", start_dir, self._filter_for_other(side))
        if path:
            self._set_file(side, path, auto_compare=True)

    def _set_file(self, side: str, path: str, auto_compare: bool = False) -> None:
        if self._busy:
            # 允许在 OCR/比较过程中直接改拖文件：记录最后一次拖入，取消当前任务，
            # 等线程安全结束后立即应用新文件并重新比较。
            self._pending_drop = (side, path, auto_compare)
            self._send_toast("正在切换文件，请稍候…")
            self._cancel_worker()
            return
        try:
            info = classify(path)
        except IncompatibleFilesError as exc:
            QMessageBox.warning(self, "不支持的文件", str(exc))
            return
        if not info.exists:
            QMessageBox.warning(self, "文件不存在", f"文件不存在：\n{info.path}")
            return
        if side == "left":
            self._left_path = info.path
            self.left_edit.setText(info.path)
            self.left_edit.setCursorPosition(0)
            self.left_edit.setToolTip(info.path)
            self.left_pane.set_file(info)
            self.left_pane.set_content(None)
        else:
            self._right_path = info.path
            self.right_edit.setText(info.path)
            self.right_edit.setCursorPosition(0)
            self.right_edit.setToolTip(info.path)
            self.right_pane.set_file(info)
            self.right_pane.set_content(None)
        self._set_focus_side(side)
        self._result = None
        self._mode = None
        self._diffs = []
        self._idx = -1
        self._settings.image.text_overrides = {}
        self._settings.image.manual_pairs = []
        self.warning_bar.setVisible(False)
        self._warnings = []
        self._update_index_label()
        self._apply_mode_controls()
        self._clear_views()
        self._update_ui_state()
        if auto_compare and self._left_path and self._right_path:
            self._start_compare()
        else:
            self.lbl_state.setText(f"已选择 {info.name}；拖入另一侧文件后将自动比较。")
            if info.kind == FileKind.IMAGE:
                self._show_image_previews("等待另一侧图片…")
                self._prefetch_image_ocr(info.path)

    def _reload_side(self, side: str) -> None:
        path = self._left_path if side == "left" else self._right_path
        if not path:
            return
        try:
            info = classify(path)
            if side == "left":
                self.left_pane.set_file(info)
                self.left_edit.setText(info.path)
                self.left_edit.setCursorPosition(0)
            else:
                self.right_pane.set_file(info)
                self.right_edit.setText(info.path)
                self.right_edit.setCursorPosition(0)
            if self._left_path and self._right_path:
                self._start_compare()
            else:
                self.lbl_state.setText("已重新加载文件；等待另一侧文件。")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "重新加载失败", str(exc))

    def _swap(self) -> None:
        self._left_path, self._right_path = self._right_path, self._left_path
        self.left_edit.setText(self._left_path)
        self.left_edit.setCursorPosition(0)
        self.right_edit.setText(self._right_path)
        self.right_edit.setCursorPosition(0)
        self.left_edit.setToolTip(self._left_path)
        self.right_edit.setToolTip(self._right_path)
        for path, pane in ((self._left_path, self.left_pane), (self._right_path, self.right_pane)):
            if path:
                try:
                    pane.set_file(classify(path))
                except Exception:
                    pane.set_file(None)
            else:
                pane.set_file(None)
        self._clear_views()
        self._send_toast("已交换左右文件。")
        if self._left_path and self._right_path:
            QTimer.singleShot(30, self._start_compare)

    def _set_focus_side(self, side: str) -> None:
        self._last_focus = side

    # ================================================================== clipboard
    def _paste_clipboard(self) -> None:
        cb = QGuiApplication.clipboard()
        target = "left" if self._focus_in_pane(self.left_pane) else "right"
        if cb.mimeData().hasImage():
            img = cb.image()
            if img.isNull():
                return
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            path = str(Path(self._temp_dir) / f"剪贴板截图-{stamp}.png")
            if img.save(path, "PNG"):
                self._set_file(target, path, auto_compare=True)
                self.lbl_state.setText(f"已粘贴剪贴板截图到 {'A' if target == 'left' else 'B'} 侧。")
            return
        if cb.mimeData().hasUrls():
            for url in cb.mimeData().urls():
                if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in {
                    ".txt", ".csv", ".xls", ".xlsx", ".png", ".jpg", ".jpeg", ".bmp",
                }:
                    self._set_file(target, url.toLocalFile(), auto_compare=True)
                    return
        self._send_toast("剪贴板中没有可用的图片或支持的文件。")

    def _focus_in_pane(self, pane: SidePane) -> bool:
        fw = QApplication.focusWidget()
        return bool(fw is not None and (pane is fw or pane.isAncestorOf(fw)))

    def _send_toast(self, message: str) -> None:
        self.lbl_state.setText(message)

    # ================================================================== compare
    def _start_compare(self) -> None:
        if self._busy:
            return
        if not self._left_path or not self._right_path:
            QMessageBox.information(self, "尚未选择文件", "请先在左右两侧选择要比较的文件。")
            return
        try:
            mode = determine_mode(self._left_path, self._right_path)
        except IncompatibleFilesError as exc:
            QMessageBox.warning(self, "文件类型不兼容", str(exc))
            self.lbl_state.setText("文件类型不兼容，未执行比较。")
            return
        self._mode = mode
        self._view = "table" if mode in (CompareMode.CSV, CompareMode.EXCEL) else (
            "image" if mode == CompareMode.IMAGE else "text"
        )
        self._result = None
        self._diffs = []
        self._idx = -1
        self.warning_bar.setVisible(False)
        self._warnings = []
        self._apply_mode_controls()
        self._clear_views()
        if mode == CompareMode.IMAGE:
            self._show_image_previews("正在准备 OCR…")
        else:
            self.left_pane.show_placeholder("正在读取并比较…")
            self.right_pane.show_placeholder("正在读取并比较…")
        self._set_busy(True, f"正在准备{mode_label(mode)}…", cancellable=True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        settings = copy.deepcopy(self._settings)
        settings.sync_scroll = self.cb_sync.isChecked()
        settings.only_diffs = self.cb_only.isChecked()
        self._save_settings()
        ocr = self._get_ocr_service() if mode == CompareMode.IMAGE else None
        self._worker = CompareWorker(mode, self._left_path, self._right_path, settings, ocr, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_compare_succeeded)
        self._worker.failed.connect(self._on_compare_failed)
        self._worker.cancelled.connect(self._on_compare_cancelled)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _get_ocr_service(self):
        if self._ocr_service is None:
            from PySide6.QtCore import QStandardPaths

            from ..ocr.engine import OcrService

            cache_dir = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.CacheLocation
            )
            self._ocr_service = OcrService(cache_dir=cache_dir or None)
        return self._ocr_service

    def _cancel_worker(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.lbl_state.setText("正在取消…")
            self.btn_cancel_worker.setEnabled(False)

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        self.lbl_state.setText(message)
        if self._mode == CompareMode.IMAGE:
            self.lbl_ocr.setText(f"OCR：{message}")

    def _on_compare_succeeded(self, result: ComparisonResult) -> None:
        self._result = result
        self._mode = result.mode
        self._set_busy(False, "", cancellable=False)
        try:
            self._build_views(result)
        except Exception as exc:  # noqa: BLE001
            import traceback

            QMessageBox.critical(self, "显示比较结果失败", f"{exc}\n\n{traceback.format_exc(limit=5)}")
            self.lbl_state.setText(f"比较完成，但显示失败：{exc}")
            return
        self._update_type_label()
        count = len(self._diffs)
        self._set_diff_count(count)
        self.lbl_ocr.setText("")
        if result.mode == CompareMode.IMAGE:
            self.lbl_ocr.setText("OCR：RapidOCR（CPU，离线）")
            self.lbl_ocr.setToolTip(
                f"{result.image.left.engine if result.image else 'RapidOCR'}；"
                "模型随安装包分发，无需联网"
            )
        warnings = list(dict.fromkeys(result.warnings))
        self._set_warnings(warnings)
        self.lbl_state.setText(
            f"{mode_label(result.mode)}完成，用时 {result.elapsed:.1f} 秒"
            + ("，发现差异。" if count else "，内容一致。")
        )
        self._apply_mode_controls()
        if self._diffs:
            self._show_diff(0)
        else:
            self._idx = -1
            self._update_index_label()
        self._update_ui_state()

    def _on_compare_failed(self, message: str) -> None:
        self._set_busy(False, "", cancellable=False)
        self.lbl_state.setText("比较失败。")
        QMessageBox.critical(self, "比较失败", message)
        self._update_ui_state()

    def _on_compare_cancelled(self) -> None:
        self._set_busy(False, "", cancellable=False)
        self.lbl_state.setText("比较已取消，未生成结果。")
        self.progress.setVisible(False)
        self._update_ui_state()

    def _on_worker_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self.progress.setVisible(False)
        self.btn_cancel_worker.setVisible(False)
        pending = self._pending_drop
        self._pending_drop = None
        if pending is not None:
            side, path, auto_compare = pending
            QTimer.singleShot(0, lambda: self._set_file(side, path, auto_compare))

    def _set_busy(self, busy: bool, message: str = "", cancellable: bool = False) -> None:
        self._busy = busy
        self.file_panel.setEnabled(not busy)
        self.btn_compare.setEnabled(not busy and bool(self._left_path and self._right_path))
        self.progress.setVisible(busy)
        self.btn_cancel_worker.setVisible(busy and cancellable)
        self.btn_cancel_worker.setEnabled(True)
        if message:
            self.lbl_state.setText(message)
        if busy and not self._cursor_overridden:
            QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
            self._cursor_overridden = True
        elif not busy and self._cursor_overridden:
            QApplication.restoreOverrideCursor()
            self._cursor_overridden = False
        self._update_ui_state()

    # ================================================================== views
    def _clear_views(self) -> None:
        self.left_pane.set_content(None, "选择或拖入基准文件 A")
        self.right_pane.set_content(None, "选择或拖入待比较文件 B")
        self._text_views = {}
        self._sheet_sides = {}
        self._image_panes = {}
        self._preview_panes = {}
        self._csv_stacks = {}
        self._diffs = []
        self._idx = -1
        self._update_index_label()

    def _prefetch_image_ocr(self, path: str) -> None:
        """拖入第一张图片后在后台预识别，等待第二张时隐藏模型/推理耗时。"""
        if not path or path in self._prefetching:
            return
        self._prefetching.add(path)
        try:
            svc = self._get_ocr_service()
        except Exception:
            self._prefetching.discard(path)
            return

        def run() -> None:
            try:
                svc.warmup(path)
                svc.recognize(path)
            except Exception:
                pass
            finally:
                self._prefetching.discard(path)

        threading.Thread(target=run, name="engineerdiff-ocr-prefetch", daemon=True).start()

    def _show_image_previews(self, status_text: str) -> None:
        """图片 OCR 前先显示原图，拖入/开始比较后不再空白等待。"""
        try:
            from .image_view import ImagePane
        except Exception:
            return
        self._preview_panes = {}
        for side, path, pane in (
            ("left", self._left_path, self.left_pane),
            ("right", self._right_path, self.right_pane),
        ):
            if not path:
                continue
            try:
                from ..file_loader import classify as _classify

                if _classify(path).kind != FileKind.IMAGE:
                    continue
            except Exception:
                continue
            view = ImagePane()
            view.set_preview(path)
            pane.set_content(view)
            self._preview_panes[side] = view
            image = QImage(path)
            if not image.isNull():
                pane.set_meta(f"{image.width()} × {image.height()} px · {status_text}")
            else:
                pane.set_meta(status_text)
        self._update_zoom_label()

    def _active_image_panes(self) -> dict[str, object]:
        return self._image_panes if self._image_panes else self._preview_panes

    def _build_views(self, result: ComparisonResult) -> None:
        self._clear_views()
        if result.mode == CompareMode.TEXT and result.text is not None:
            self._build_text_views(result)
            self._view = "text"
        elif result.mode == CompareMode.CSV and result.csv is not None:
            self._build_csv_views(result)
            self._view = self.combo_view.currentData() or "table"
        elif result.mode == CompareMode.EXCEL and result.workbook is not None:
            self._build_excel_views(result)
            self._view = "table"
        elif result.mode == CompareMode.IMAGE and result.image is not None:
            self._build_image_views(result)
            self._view = "image"
        else:
            raise RuntimeError("比较结果缺少对应模式的视图数据。")
        if self._mode in (CompareMode.CSV, CompareMode.EXCEL):
            self._apply_sheet_zoom()
        self._apply_view_filters()
        self._refresh_diffs()

    def _new_text_view(self, side: str, text_cmp) -> TextView:
        tv = TextView()
        tv.set_side(text_cmp.left if side == "left" else text_cmp.right)
        tv.set_only_diffs(self.cb_only.isChecked())
        tv.line_activated.connect(
            lambda row, s=side: self._on_text_line_activated(s, row)
        )
        return tv

    def _on_text_line_activated(self, side: str, display_row: int) -> None:
        self._set_focus_side(side)
        tv = self._text_views.get(side)
        if tv is None or tv.side() is None or self._result is None:
            return
        lines = tv.side().lines
        if not (0 <= display_row < len(lines)):
            return
        diff_id = lines[display_row].diff_id
        if diff_id is None:
            return
        for i, diff in enumerate(self._diffs):
            if diff.id == diff_id:
                self._show_diff(i)
                return

    def _connect_text_scroll(self) -> None:
        if set(self._text_views) != {"left", "right"}:
            return
        lbar = self._text_views["left"].verticalScrollBar()
        rbar = self._text_views["right"].verticalScrollBar()
        lbar.valueChanged.connect(lambda _v: self._sync_scroll("text", "left"))
        rbar.valueChanged.connect(lambda _v: self._sync_scroll("text", "right"))

    def _build_text_views(self, result: ComparisonResult) -> None:
        self.combo_view.setVisible(False)
        self.image_tools.setVisible(False)
        for side, pane in (("left", self.left_pane), ("right", self.right_pane)):
            tv = self._new_text_view(side, result.text)
            pane.set_content(tv)
            self._text_views[side] = tv
        self._connect_text_scroll()
        left = result.text.left
        self.left_pane.set_meta(f"{left.encoding} · {left.original_line_count} 行")
        right = result.text.right
        self.right_pane.set_meta(f"{right.encoding} · {right.original_line_count} 行")

    def _build_csv_views(self, result: ComparisonResult) -> None:
        from .spreadsheet_side import SpreadsheetSide

        self.combo_view.setVisible(True)
        self.image_tools.setVisible(False)
        index = 1 if self.combo_view.currentData() == "text" else 0
        for side, pane, wb in (
            ("left", self.left_pane, result.csv.table.left),
            ("right", self.right_pane, result.csv.table.right),
        ):
            stack = QStackedWidget()
            tv = self._new_text_view(side, result.text)
            ss = SpreadsheetSide()
            ss.load(wb, result.csv.table, side, show_tabs=False)
            ss.scroll_changed.connect(lambda vr, hr, s=side: self._on_sheet_scrolled(s, vr, hr))
            ss.grid().cell_clicked.connect(
                lambda row, col, s=side: self._on_grid_cell_clicked(s, row, col)
            )
            stack.addWidget(ss)
            stack.addWidget(tv)
            stack.setCurrentIndex(index)
            pane.set_content(stack)
            self._text_views[side] = tv
            self._sheet_sides[side] = ss
            self._csv_stacks[side] = stack
        self._connect_text_scroll()
        self.left_pane.set_meta(f"CSV · 分隔符 {result.csv.delimiter!r}")
        self.right_pane.set_meta(f"CSV · 分隔符 {result.csv.delimiter!r}")

    def _build_excel_views(self, result: ComparisonResult) -> None:
        from .spreadsheet_side import SpreadsheetSide

        self.combo_view.setVisible(False)
        self.image_tools.setVisible(False)
        cmp = result.workbook
        for side, pane, wb in (
            ("left", self.left_pane, cmp.left),
            ("right", self.right_pane, cmp.right),
        ):
            ss = SpreadsheetSide()
            ss.load(wb, cmp, side, show_tabs=True)
            ss.sheet_selected.connect(lambda name, s=side: self._on_sheet_selected(s, name))
            ss.scroll_changed.connect(lambda vr, hr, s=side: self._on_sheet_scrolled(s, vr, hr))
            ss.grid().cell_clicked.connect(
                lambda row, col, s=side: self._on_grid_cell_clicked(s, row, col)
            )
            pane.set_content(ss)
            self._sheet_sides[side] = ss
        initial = None
        for sc in cmp.sheets:
            if sc.status != SheetStatus.EQUAL and sc.left is not None:
                initial = sc.name
                break
        if initial is None and cmp.left.sheets:
            initial = cmp.left.sheets[0].name
        if initial is not None:
            self._show_sheet_on_both(initial)
        self.left_pane.set_meta(f"{cmp.left.fmt.upper()} · {len(cmp.left.sheets)} 个工作表")
        self.right_pane.set_meta(f"{cmp.right.fmt.upper()} · {len(cmp.right.sheets)} 个工作表")

    def _build_image_views(self, result: ComparisonResult) -> None:
        from .image_view import ImagePane

        self.combo_view.setVisible(False)
        self.image_tools.setVisible(True)
        self.cb_boxes.blockSignals(True)
        self.cb_boxes.setChecked(self._settings.image.show_boxes)
        self.cb_boxes.blockSignals(False)
        img = result.image
        for side, pane, page in (
            ("left", self.left_pane, img.left),
            ("right", self.right_pane, img.right),
        ):
            ip = ImagePane()
            ip.set_comparison(page, img, side, show_boxes=self.cb_boxes.isChecked())
            pane.set_content(ip)
            self._image_panes[side] = ip
        self.left_pane.set_meta(f"{img.left.width} × {img.left.height} px · {len(img.left.boxes)} 个文字块")
        self.right_pane.set_meta(f"{img.right.width} × {img.right.height} px · {len(img.right.boxes)} 个文字块")
        self._connect_image_views()

    def _connect_image_views(self) -> None:
        if set(self._image_panes) != {"left", "right"}:
            return
        self._image_sync_ready = False
        for side, pane in self._image_panes.items():
            pane.set_sync_zoom(self.cb_sync.isChecked())
            pane.zoom_changed.connect(lambda _z, s=side: self._sync_image_zoom(s))
            pane.scroll_changed.connect(
                lambda v, h, s=side: self._sync_image_scroll(s, v, h)
            )
        # 初次显示时两侧各自“适应窗口”；不同分辨率下缩放比例本来就不同，
        # 等布局稳定后再启用同步缩放，避免小图把大图也放大导致裁切。
        QTimer.singleShot(350, self._enable_image_sync)
        self._update_zoom_label()

    def _enable_image_sync(self) -> None:
        self._image_sync_ready = True
        self._update_zoom_label()

    def _sync_image_zoom(self, source: str) -> None:
        if not self._image_sync_ready:
            return
        if not self.cb_sync.isChecked() or self._sync_guard:
            return
        other = self._image_panes.get("right" if source == "left" else "left")
        if other is None:
            return
        zoom = self._image_panes[source].zoom()
        self._sync_guard = True
        try:
            other.set_zoom(zoom)
        finally:
            self._sync_guard = False
        self._update_zoom_label()

    def _sync_image_scroll(self, source: str, v: float, h: float) -> None:
        if not self.cb_sync.isChecked() or self._sync_guard:
            return
        other = self._image_panes.get("right" if source == "left" else "left")
        if other is None:
            return
        self._sync_guard = True
        try:
            other.set_scroll_ratio(v, h)
        finally:
            self._sync_guard = False

    # ================================================================== CSV/Excel
    def _on_grid_cell_clicked(self, side: str, row: int, col: int) -> None:
        self._set_focus_side(side)
        ss = self._sheet_sides.get(side)
        if ss is None:
            return
        diff = ss.grid().diff_at(row, col)
        if diff is None:
            return
        for i, item in enumerate(self._diffs):
            if item.id == diff.id:
                self._show_diff(i)
                return
        sheet_name = self._diff_sheet_name(diff)
        if sheet_name:
            self._show_sheet_on_both(sheet_name)

    def _on_sheet_selected(self, side: str, name: str) -> None:
        if self._sync_guard or self._mode != CompareMode.EXCEL:
            return
        if not self.cb_sheet_sync.isChecked():
            return
        other = "right" if side == "left" else "left"
        target = self._sheet_sides.get(other)
        if target is None:
            return
        self._sync_guard = True
        try:
            target.show_sheet(name)
        finally:
            self._sync_guard = False

    def _show_sheet_on_both(self, name: str | None) -> None:
        if name is None:
            return
        for side, ss in self._sheet_sides.items():
            try:
                ss.show_sheet(name)
            except Exception:
                pass

    def _on_sheet_scrolled(self, side: str, vr: float, hr: float) -> None:
        if self._sync_guard or not self.cb_sync.isChecked():
            return
        other = self._sheet_sides.get("right" if side == "left" else "left")
        if other is None:
            return
        self._sync_guard = True
        try:
            setter = getattr(other, "set_scroll_ratio", None)
            if callable(setter):
                setter(vr, hr)
            else:
                grid = other.grid()
                for bar, ratio in (
                    (grid.verticalScrollBar(), vr),
                    (grid.horizontalScrollBar(), hr),
                ):
                    span = bar.maximum() - bar.minimum()
                    if span > 0:
                        bar.setValue(int(bar.minimum() + max(0.0, min(1.0, ratio)) * span))
        finally:
            self._sync_guard = False

    def _on_view_changed(self, _index: int) -> None:
        if self._mode != CompareMode.CSV or not self._csv_stacks:
            return
        self._view = self.combo_view.currentData() or "table"
        self.sheet_tools.setVisible(self._view == "table")
        for stack in self._csv_stacks.values():
            stack.setCurrentIndex(1 if self._view == "text" else 0)
        self._apply_view_filters()
        self._refresh_diffs()
        if self._diffs:
            self._show_diff(0)
        self.lbl_state.setText("已切换 CSV 视图。")

    # ================================================================== filters / nav
    def _apply_view_filters(self) -> None:
        only = self.cb_only.isChecked()
        for tv in self._text_views.values():
            tv.set_only_diffs(only)
        if self._mode in (CompareMode.EXCEL, CompareMode.CSV):
            for ss in self._sheet_sides.values():
                try:
                    ss.set_only_diffs(only)
                except Exception:
                    pass

    def _refresh_diffs(self) -> None:
        if self._result is None:
            self._diffs = []
        else:
            self._diffs = self._result.diffs_for_view(self._view)
        self._idx = 0 if self._diffs else -1
        self._update_index_label()
        self._set_diff_count(len(self._diffs))

    def _set_diff_count(self, count: int) -> None:
        p = theme.palette()
        self.lbl_diff.setText(f"差异 {count} 处")
        if count == 0:
            self.lbl_diff.setStyleSheet(f"font-weight:600;color:{p.success};")
            self.lbl_diff.setToolTip("两侧内容一致")
        else:
            self.lbl_diff.setStyleSheet(f"font-weight:600;color:{p.diff_red};")
            self.lbl_diff.setToolTip(f"{count} 处差异")

    def _update_index_label(self) -> None:
        if self._diffs and 0 <= self._idx < len(self._diffs):
            self.lbl_index.setText(f"{self._idx + 1} / {len(self._diffs)}")
        else:
            self.lbl_index.setText(f"0 / {len(self._diffs)}")
        has = bool(self._diffs)
        self.btn_prev.setEnabled(has)
        self.btn_next.setEnabled(has)

    def _step_diff(self, direction: int) -> None:
        if not self._diffs:
            return
        if self._idx < 0:
            self._idx = 0
        else:
            self._idx = (self._idx + direction) % len(self._diffs)
        self._show_diff(self._idx)

    def _show_diff(self, index: int) -> None:
        if not (0 <= index < len(self._diffs)):
            return
        self._idx = index
        diff = self._diffs[index]
        self._update_index_label()
        try:
            if self._view == "text":
                for side, tv in self._text_views.items():
                    tv.reveal(diff, side)
            elif self._view == "image":
                for pane in self._image_panes.values():
                    pane.reveal(diff)
            else:
                sheet_name = self._diff_sheet_name(diff)
                if sheet_name:
                    self._show_sheet_on_both(sheet_name)
                for ss in self._sheet_sides.values():
                    ss.reveal(diff)
        except Exception as exc:  # noqa: BLE001
            self.lbl_state.setText(f"定位差异失败：{exc}")
        title = diff.title or diff.id
        detail = f"：{diff.detail}" if diff.detail else ""
        message = f"差异 {index + 1}/{len(self._diffs)} — {title}{detail}"
        if len(message) > 96:
            message = message[:95] + "…"
        self.lbl_state.setText(message)
        self.lbl_state.setToolTip(f"{title}{detail}")
        if diff.severity.value == "uncertain":
            self.lbl_state.setStyleSheet("color:#9D5D00;")
        else:
            self.lbl_state.setStyleSheet("color:#424242;")

    @staticmethod
    def _diff_sheet_name(diff: DiffItem) -> str | None:
        for loc in (diff.left, diff.right):
            if loc is not None and loc.sheet:
                return loc.sheet
        return None

    def _on_only_diffs_toggled(self, checked: bool) -> None:
        self._settings.only_diffs = checked
        self._save_settings()
        self._apply_view_filters()
        if self._result is not None and not self._busy:
            self._refresh_diffs()
            if self._diffs:
                self._show_diff(0)

    def _on_sync_toggled(self, checked: bool) -> None:
        self._settings.sync_scroll = checked
        self._save_settings()
        for pane in self._image_panes.values():
            pane.set_sync_zoom(checked)

    def _on_sheet_sync_toggled(self, checked: bool) -> None:
        self._settings.sync_sheets = checked
        self._save_settings()
        if checked and self._mode == CompareMode.EXCEL:
            current = self._sheet_sides.get("left")
            if current is not None:
                name = current.current_sheet()
                if name:
                    self._show_sheet_on_both(name)

    def _sync_scroll(self, kind: str, source: str) -> None:
        if self._sync_guard or not self.cb_sync.isChecked():
            return
        if kind == "text" and set(self._text_views) == {"left", "right"}:
            other = "right" if source == "left" else "left"
            src_bar = self._text_views[source].verticalScrollBar()
            dst_bar = self._text_views[other].verticalScrollBar()
            self._sync_guard = True
            try:
                dst_bar.setValue(src_bar.value())
                src_h = self._text_views[source].horizontalScrollBar()
                dst_h = self._text_views[other].horizontalScrollBar()
                dst_h.setValue(src_h.value())
            finally:
                self._sync_guard = False

    # ================================================================== warnings
    def _set_warnings(self, warnings: list[str]) -> None:
        self._warnings = warnings
        if warnings:
            self.warning_bar.setVisible(True)
            text = warnings[0]
            if len(text) > 110:
                text = text[:110] + "…"
            self.lbl_warning.setText(f"{text}" + (f"（共 {len(warnings)} 条）" if len(warnings) > 1 else ""))
            self.btn_warning.setVisible(len(warnings) > 0)
        else:
            self.warning_bar.setVisible(False)
        self.lbl_warn_count.setText(f"提示 {len(warnings)} 条" if warnings else "")

    def _show_warnings(self) -> None:
        warnings = getattr(self, "_warnings", [])
        if warnings:
            QMessageBox.information(self, "比较提示", "\n\n".join(f"• {w}" for w in warnings))

    # ================================================================== table zoom
    def _sheet_zoom_action(self, direction: int) -> None:
        if direction == 0:
            self._sheet_zoom = 1.0
        else:
            self._sheet_zoom = max(0.5, min(2.5, self._sheet_zoom + 0.1 * direction))
        self._apply_sheet_zoom()

    def _apply_sheet_zoom(self) -> None:
        for pane in self._sheet_sides.values():
            try:
                pane.set_zoom(self._sheet_zoom)
            except Exception:
                pass
        if self.sheet_tools.isVisible():
            self.btn_sheet_zoom_reset.setText(f"{round(self._sheet_zoom * 100)}%")

    def _reset_sheet_zoom(self) -> None:
        self._sheet_zoom = 1.0
        self._apply_sheet_zoom()

    # ================================================================== image controls
    def _image_action(self, action: str) -> None:
        panes = list(self._active_image_panes().values())
        if not panes:
            return
        # 适应窗口/原始尺寸对两张不同尺寸的图分别执行；缩放按钮在“同步”开启时
        # 只操作一侧，由同步信号带动另一侧，避免重复累乘。
        if action in ("fit", "actual"):
            self._sync_guard = True
            try:
                for p in panes:
                    if action == "fit":
                        p.fit_to_window()
                    else:
                        p.actual_size()
            finally:
                self._sync_guard = False
        elif action in ("zoom_in", "zoom_out"):
            active = self._active_image_panes()
            targets = [active.get("left")] if self.cb_sync.isChecked() else panes
            for p in targets:
                if p is None:
                    continue
                if action == "zoom_in":
                    p.zoom_in()
                else:
                    p.zoom_out()
        QTimer.singleShot(0, self._update_zoom_label)

    def _update_zoom_label(self) -> None:
        try:
            zooms = [p.zoom() for p in self._active_image_panes().values()]
        except Exception:
            return
        if not zooms:
            return
        if len(zooms) == 1:
            self.lbl_zoom.setText(f"{round(zooms[0] * 100)}%")
        else:
            a, b = (round(zooms[0] * 100), round(zooms[1] * 100))
            self.lbl_zoom.setText(f"{a}% / {b}%")

    def _on_show_boxes(self, checked: bool) -> None:
        for pane in self._image_panes.values():
            pane.set_show_boxes(checked)
        self._settings.image.show_boxes = checked
        self._save_settings()

    def _open_ocr_correction(self) -> None:
        if self._result is None or self._result.image is None:
            return
        try:
            from .ocr_dialog import OcrCorrectionDialog
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "OCR 校对", f"OCR 校对组件不可用：{exc}")
            return
        try:
            dlg = OcrCorrectionDialog(
                self, self._result.image, self._ocr_service, self._settings.image
            )
            if dlg.exec():
                corrected = dlg.corrected_options()
                if corrected is not None:
                    self._settings.image = corrected
                    self.lbl_state.setText("OCR 修正已应用，正在重新比较…")
                    self._start_compare()
        except Exception as exc:  # noqa: BLE001
            import traceback

            QMessageBox.warning(
                self, "OCR 校对", f"打开 OCR 校对失败：{exc}\n\n{traceback.format_exc(limit=5)}"
            )

    # ================================================================== ui state
    def _apply_mode_controls(self) -> None:
        mode = self._mode
        is_csv = mode == CompareMode.CSV
        is_excel = mode == CompareMode.EXCEL
        is_image = mode == CompareMode.IMAGE
        self.combo_view.setVisible(is_csv)
        self.cb_sheet_sync.setVisible(is_excel)
        self.sheet_tools.setVisible(is_csv or is_excel)
        self.image_tools.setVisible(is_image)
        self.btn_sheet_zoom_reset.setText(f"{round(self._sheet_zoom * 100)}%")
        self.cb_only.setEnabled(not is_image)
        self.cb_sync.setEnabled(True)
        self.btn_ocr_fix.setEnabled(is_image)
        if is_csv:
            self.cb_only.setEnabled(True)
        self._update_zoom_label()

    def _update_type_label(self) -> None:
        if not self._left_path or not self._right_path:
            self.lbl_type.setText("未选择文件")
            return
        try:
            a = classify(self._left_path)
            b = classify(self._right_path)
            self.lbl_type.setText(f"{a.kind.value.upper()} ↔ {b.kind.value.upper()}")
        except Exception:
            self.lbl_type.setText("文件类型未知")

    def _update_ui_state(self) -> None:
        ready = bool(self._left_path and self._right_path) and not self._busy
        self.btn_compare.setEnabled(ready)
        self.btn_swap.setEnabled(not self._busy and bool(self._left_path or self._right_path))
        self.btn_pick_left.setEnabled(not self._busy)
        self.btn_pick_right.setEnabled(not self._busy)
        self.btn_reload_left.setEnabled(not self._busy and bool(self._left_path))
        self.btn_reload_right.setEnabled(not self._busy and bool(self._right_path))
        self.btn_settings.setEnabled(not self._busy)
        self.btn_compare.setText("重新比较" if self._result is not None else "开始比较")
        if not ready and not self._busy:
            if not (self._left_path and self._right_path):
                self.btn_compare.setToolTip("请先选择左右两侧文件")
            else:
                self.btn_compare.setToolTip("")
        else:
            self.btn_compare.setToolTip("比较（Ctrl+Enter）")
        self.btn_ocr_fix.setEnabled(self._mode == CompareMode.IMAGE and self._result is not None)

    # ================================================================== close
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._save_settings()
        self._qsettings.setValue("window/geometry", self.saveGeometry())
        self._qsettings.setValue("window/splitter", self.splitter.saveState())
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            # OCR 推理不可中断时等待其安全结束，避免 QThread 被销毁导致崩溃
            self._worker.wait(8000)
            if self._worker.isRunning():
                self._worker.wait()
        try:
            import shutil

            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception:
            pass
        super().closeEvent(event)
