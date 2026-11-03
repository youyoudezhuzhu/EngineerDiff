"""比较设置对话框。"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..models import AppSettings
from . import theme


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("比较设置")
        self.setMinimumWidth(520)
        self._settings = copy.deepcopy(settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        tip = QLabel("设置将在下一次比较（或点击“重新比较”）时生效。默认严格比较，不忽略真实字符差异。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#616161;")
        layout.addWidget(tip)

        appearance_group = QGroupBox("外观")
        af = QFormLayout(appearance_group)
        self.cb_theme = QComboBox()
        self.cb_theme.addItem("跟随 Windows 设置", "system")
        self.cb_theme.addItem("浅色", "light")
        self.cb_theme.addItem("深色", "dark")
        af.addRow("界面主题：", self.cb_theme)
        layout.addWidget(appearance_group)

        text_group = QGroupBox("文本比较")
        tf = QFormLayout(text_group)
        tf.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.cb_line_endings = QCheckBox("忽略换行符差异（CRLF / LF / 末尾换行）")
        self.cb_outer_space = QCheckBox("忽略每行前后空格")
        self.cb_all_space = QCheckBox("忽略全部空白字符")
        self.cb_case = QCheckBox("忽略大小写")
        tf.addRow(self.cb_line_endings)
        tf.addRow(self.cb_outer_space)
        tf.addRow(self.cb_all_space)
        tf.addRow(self.cb_case)
        layout.addWidget(text_group)

        sheet_group = QGroupBox("表格 / Excel 比较")
        sf = QFormLayout(sheet_group)
        self.cb_formula = QComboBox()
        self.cb_formula.addItem("按公式表达式比较（推荐，严格）", "formula")
        self.cb_formula.addItem("按缓存计算结果比较", "value")
        self.cb_formula.setToolTip(
            "公式表达式模式下，两侧公式不同即判定差异；缓存值模式下比较保存的计算结果，"
            "缓存缺失会以橙色提示人工复核。"
        )
        self.spin_tolerance = QDoubleSpinBox()
        self.spin_tolerance.setDecimals(6)
        self.spin_tolerance.setRange(0.0, 1e9)
        self.spin_tolerance.setSingleStep(1e-6)
        self.spin_tolerance.setSpecialValueText("0（严格）")
        self.spin_tolerance.setToolTip("数值容差：非 0 时，两侧数值差异在容差内视为相同。默认 0，任何差异都标红。")
        self.cb_smart_rows = QCheckBox("智能行对齐（实验性，用于识别整行插入/删除）")
        self.cb_smart_rows.setToolTip("默认关闭。严格按单元格坐标比较；开启后尝试对齐整行，不改变原始单元格对应关系。")
        sf.addRow("公式比较：", self.cb_formula)
        sf.addRow("数值容差：", self.spin_tolerance)
        sf.addRow(self.cb_smart_rows)
        layout.addWidget(sheet_group)

        ocr_group = QGroupBox("图片 OCR 比较")
        of = QFormLayout(ocr_group)
        self.spin_confidence = QDoubleSpinBox()
        self.spin_confidence.setRange(0.30, 0.99)
        self.spin_confidence.setSingleStep(0.05)
        self.spin_confidence.setDecimals(2)
        self.spin_confidence.setToolTip("低于该置信度的 OCR 结果以橙色提示，需要人工复核。")
        self.cb_show_boxes = QCheckBox("比较后默认显示 OCR 文字框")
        of.addRow("人工复核阈值：", self.spin_confidence)
        of.addRow(self.cb_show_boxes)
        self.btn_clear_cache = QPushButton("清除 OCR 缓存")
        self.btn_clear_cache.setObjectName("tool")
        self.lbl_cache = QLabel("")
        self.lbl_cache.setStyleSheet("color:#757575;")
        cache_row = QHBoxLayout()
        cache_row.addWidget(self.btn_clear_cache)
        cache_row.addWidget(self.lbl_cache, 1)
        of.addRow(cache_row)
        layout.addWidget(ocr_group)

        common_group = QGroupBox("通用")
        cf = QFormLayout(common_group)
        self.cb_sync_sheets = QCheckBox("默认同步切换 Excel 工作表")
        self.cb_sync_scroll = QCheckBox("默认同步滚动 / 同步缩放")
        cf.addRow(self.cb_sync_sheets)
        cf.addRow(self.cb_sync_scroll)
        layout.addWidget(common_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.btn_defaults = buttons.addButton("恢复默认", QDialogButtonBox.ButtonRole.ResetRole)
        self.btn_about = buttons.addButton("关于 EngineerDiff", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.btn_defaults.clicked.connect(self._restore_defaults)
        self.btn_about.clicked.connect(self._show_about)
        layout.addWidget(buttons)
        self._load(settings)

    def _load(self, s: AppSettings) -> None:
        idx = self.cb_theme.findData(s.theme)
        self.cb_theme.setCurrentIndex(max(0, idx))
        self.cb_line_endings.setChecked(s.text.ignore_line_endings)
        self.cb_outer_space.setChecked(s.text.ignore_outer_space)
        self.cb_all_space.setChecked(s.text.ignore_all_space)
        self.cb_case.setChecked(s.text.ignore_case)
        idx = self.cb_formula.findData(s.spreadsheet.formula_mode)
        self.cb_formula.setCurrentIndex(max(0, idx))
        self.spin_tolerance.setValue(s.spreadsheet.numeric_tolerance)
        self.cb_smart_rows.setChecked(s.spreadsheet.smart_rows)
        self.spin_confidence.setValue(s.image.confidence_threshold)
        self.cb_show_boxes.setChecked(s.image.show_boxes)
        self.cb_sync_sheets.setChecked(s.sync_sheets)
        self.cb_sync_scroll.setChecked(s.sync_scroll)

    def _show_about(self) -> None:
        from .about_dialog import AboutDialog

        AboutDialog(self).exec()

    def _restore_defaults(self) -> None:
        self._load(AppSettings())

    def result_settings(self) -> AppSettings:
        s = copy.deepcopy(self._settings)
        s.theme = self.cb_theme.currentData() or "system"
        s.text.ignore_line_endings = self.cb_line_endings.isChecked()
        s.text.ignore_outer_space = self.cb_outer_space.isChecked()
        s.text.ignore_all_space = self.cb_all_space.isChecked()
        s.text.ignore_case = self.cb_case.isChecked()
        s.spreadsheet.formula_mode = self.cb_formula.currentData()
        s.spreadsheet.numeric_tolerance = self.spin_tolerance.value()
        s.spreadsheet.smart_rows = self.cb_smart_rows.isChecked()
        s.image.confidence_threshold = self.spin_confidence.value()
        s.image.show_boxes = self.cb_show_boxes.isChecked()
        s.sync_sheets = self.cb_sync_sheets.isChecked()
        s.sync_scroll = self.cb_sync_scroll.isChecked()
        return s
