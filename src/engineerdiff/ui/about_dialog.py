"""关于 EngineerDiff。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from .. import version
from . import theme


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"关于 {version.APP_NAME}")
        self.setFixedWidth(430)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 18)
        root.setSpacing(8)

        icon = QLabel("ED")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(56, 56)
        root.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addSpacing(6)

        self.lbl_name = QLabel(version.APP_NAME)
        self.lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lbl_name)
        self.lbl_desc = QLabel("工程文件差异比较工具")
        self.lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lbl_desc)
        self.lbl_author = QLabel(f"by {version.AUTHOR}")
        self.lbl_author.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lbl_author)
        self.lbl_version = QLabel(f"版本 {version.__version__}")
        self.lbl_version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lbl_version)
        root.addSpacing(6)
        self.lbl_note = QLabel(
            "本地离线运行，不上传文件。TXT / CSV / Excel / 图片 OCR 差异直接标注在原始内容上。\n"
            "第三方组件版权与许可证见安装目录 LICENSES 与 THIRD-PARTY-NOTICES.md。"
        )
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lbl_note)
        root.addSpacing(8)
        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        root.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.apply_theme()

    def apply_theme(self) -> None:
        p = theme.palette()
        self.setStyleSheet(f"QDialog {{ background:{p.card_bg}; }}")
        self.lbl_name.setStyleSheet(f"font-size:22pt;font-weight:600;color:{p.text};")
        self.lbl_desc.setStyleSheet(f"font-size:10.5pt;color:{p.text_secondary};")
        self.lbl_author.setStyleSheet(f"font-size:11pt;font-weight:600;color:{p.accent};")
        self.lbl_version.setStyleSheet(f"color:{p.text_secondary};")
        self.lbl_note.setStyleSheet(f"color:{p.text_secondary};font-size:9pt;line-height:1.4;")
        # 图标方块使用强调色，与主窗口品牌一致
        for child in self.findChildren(QLabel):
            if child.text() == "ED":
                child.setStyleSheet(
                    f"background:{p.accent};color:white;border-radius:12px;"
                    "font-size:19pt;font-weight:700;"
                )
