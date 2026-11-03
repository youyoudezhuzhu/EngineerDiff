"""关于 EngineerDiff。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

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

        icon_path = Path(__file__).resolve().parents[1] / "resources" / "engineerdiff.png"
        icon = QLabel()
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(72, 72)
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            icon.setPixmap(
                pixmap.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            )
        root.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addSpacing(6)

        self.lbl_name = QLabel(version.APP_NAME)
        self.lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lbl_name)
        self.lbl_desc = QLabel("工程文件差异比较工具")
        self.lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.lbl_desc)
        author_row = QHBoxLayout()
        author_row.setSpacing(10)
        author_row.addStretch(1)
        self.lbl_author = QLabel(f"by {version.AUTHOR}")
        self.lbl_author.setAlignment(Qt.AlignmentFlag.AlignCenter)
        author_row.addWidget(self.lbl_author)
        self.lbl_github = QLabel()
        self.lbl_github.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_github.setOpenExternalLinks(True)
        self.lbl_github.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.lbl_github.setToolTip("打开 GitHub 项目主页")
        author_row.addWidget(self.lbl_github)
        author_row.addStretch(1)
        root.addLayout(author_row)
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
        self.lbl_github.setText(
            f'<a href="https://github.com/youyoudezhuzhu/EngineerDiff" '
            f'style="color:{p.accent}; text-decoration:none;">GitHub</a>'
        )
        self.lbl_github.setStyleSheet("font-size:10pt;")
        self.lbl_version.setStyleSheet(f"color:{p.text_secondary};")
        self.lbl_note.setStyleSheet(f"color:{p.text_secondary};font-size:9pt;line-height:1.4;")
