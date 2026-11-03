"""EngineerDiff 应用入口。"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from . import version


def _resource_path(*parts: str) -> Path:
    return Path(__file__).resolve().parent.joinpath("resources", *parts)


def _safe_print(*args) -> None:
    try:
        if sys.stdout is not None:
            print(*args)
    except Exception:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="EngineerDiff",
        description="Windows 本地多格式文件差异比较工具（TXT/CSV/Excel/图片 OCR）",
    )
    parser.add_argument("--version", action="version", version=f"EngineerDiff {version.__version__}")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="运行内置自检（不需要显示界面），用于安装包验证",
    )
    parser.add_argument(
        "--self-test-report",
        default="",
        help="自检 JSON 报告输出路径（默认当前目录 self-test-report.json）",
    )
    parser.add_argument("--left", default="", help="启动时载入左侧文件 A")
    parser.add_argument("--right", default="", help="启动时载入右侧文件 B")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    try:
        args = build_parser().parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code or 0)

    if args.self_test:
        from .self_test import run_self_test

        report = args.self_test_report or str(Path.cwd() / "self-test-report.json")
        return run_self_test(report)

    from PySide6.QtCore import QLocale, QSettings, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .ui import theme
    from .ui.main_window import MainWindow

    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass
    app = QApplication(argv)
    app.setApplicationName(version.APP_NAME)
    app.setOrganizationName(version.ORG_NAME)
    app.setApplicationVersion(version.__version__)
    app.setStyle("Fusion")
    QLocale.setDefault(QLocale(QLocale.Language.Chinese, QLocale.Country.China))
    app.setFont(theme.ui_font(10.0))
    # 跟随系统/浅色/深色：在创建窗口前应用，避免先闪白再切深色。
    saved_mode = QSettings(version.ORG_NAME, version.APP_NAME).value("general/theme", "system")
    theme.apply_theme(str(saved_mode), app)
    icon_path = _resource_path("engineerdiff.ico")
    if not icon_path.exists():
        icon_path = _resource_path("engineerdiff.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    try:
        window = MainWindow()
        if args.left:
            window._set_file("left", args.left)
        if args.right:
            window._set_file("right", args.right)
        window.show()
        return app.exec()
    except Exception as exc:  # noqa: BLE001
        detail = traceback.format_exc()
        try:
            Path.home().joinpath(".engineerdiff-crash.log").write_text(detail, encoding="utf-8")
        except Exception:
            pass
        try:
            QMessageBox.critical(
                None,
                "EngineerDiff 启动失败",
                f"程序启动时发生错误：\n{exc}\n\n详细日志：~/.engineerdiff-crash.log",
            )
        except Exception:
            pass
        _safe_print(detail)
        return 1


if __name__ == "__main__":
    sys.exit(main())
