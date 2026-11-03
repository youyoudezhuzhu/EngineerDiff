"""Fluent 设计令牌、深浅色与导航/批量矩阵的 UI 冒烟测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from engineerdiff.models import AppSettings
from engineerdiff.ui import theme


def test_theme_tokens_and_stylesheet():
    assert theme.LIGHT.dark is False
    assert theme.DARK.dark is True
    # 差异红/待复核橙在深浅主题都必须有独立语义色
    for p in (theme.LIGHT, theme.DARK):
        assert p.diff_red and p.diff_red_bg
        assert p.warning and p.warning_bg
        assert p.diff_red != p.warning
        qss = theme.stylesheet(p)
        assert p.accent in qss
        assert "QTabBar#modeNav" in qss


def test_theme_apply_switches_palette(qapp):
    old = theme.palette()
    try:
        theme.apply_theme("dark", qapp)
        assert theme.palette().name == "dark"
        theme.apply_theme("light", qapp)
        assert theme.palette().name == "light"
    finally:
        theme.apply_theme(old.name, qapp)


@pytest.mark.gui
def test_main_navigation_and_dark_theme(qapp):
    from engineerdiff.ui.main_window import MainWindow

    theme.apply_theme("light", qapp)
    win = MainWindow()
    win.show()
    qapp.processEvents()
    assert win.windowTitle() == "EngineerDiff工程文件差异比较 by jzh"
    assert win.btn_pick_left.text() == "选择"
    assert win.btn_pick_right.text() == "选择"
    assert win.pages.count() == 2
    assert win.nav.count() == 2
    win.nav.setCurrentIndex(1)
    qapp.processEvents()
    assert win.pages.currentIndex() == 1
    theme.apply_theme("dark", qapp)
    qapp.processEvents()
    assert theme.palette().name == "dark"
    assert win.palette().color(win.palette().ColorRole.Window).lightness() < 100
    win.close()


@pytest.mark.gui
def test_batch_excel_matrix_groups_by_sheet(qapp, tmp_path: Path):
    import openpyxl

    from engineerdiff.batch import run_batch
    from engineerdiff.ui.batch_dialog import BatchCompareDialog

    def make(path: Path, version: str, sheet2: bool) -> Path:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "参数"
        ws["A1"] = "版本"
        ws["B1"] = version
        wb.create_sheet("第二页")["A1"] = "基准" if sheet2 else "候选"
        wb.save(path)
        return path

    base = make(tmp_path / "a.xlsx", "V1", True)
    cand = make(tmp_path / "b.xlsx", "V2", False)
    result = run_batch(str(base), [str(cand)], AppSettings())
    dlg = BatchCompareDialog(None, AppSettings())
    dlg._populate(result)
    assert dlg.table.rowCount() >= len(result.rows) + 2
    assert len(dlg._groups) == 2
    # 组行跨越全部列
    assert dlg.table.columnSpan(dlg._groups[0][0], 0) == dlg.table.columnCount()
    dlg.close()


def test_settings_theme_default_is_system():
    assert AppSettings().theme == "system"


@pytest.mark.gui
def test_about_dialog_shows_author_and_version(qapp):
    from engineerdiff import version
    from engineerdiff.ui.about_dialog import AboutDialog

    theme.apply_theme("light", qapp)
    dlg = AboutDialog()
    texts = [w.text() for w in dlg.findChildren(type(dlg.lbl_name))]
    assert version.APP_NAME in texts
    assert "by jzh" in texts
    assert version.__version__ in " ".join(texts)
    dlg.close()


def test_author_metadata():
    from engineerdiff import version

    assert version.AUTHOR == "jzh"


@pytest.mark.gui
def test_batch_baseline_can_be_set_from_drop_paths(qapp, tmp_path: Path):
    from engineerdiff.ui.batch_dialog import BatchCompareDialog

    a = tmp_path / "a.txt"; a.write_text("a\n", encoding="utf-8")
    b = tmp_path / "b.txt"; b.write_text("b\n", encoding="utf-8")
    c = tmp_path / "c.txt"; c.write_text("c\n", encoding="utf-8")
    dlg = BatchCompareDialog(None, AppSettings(), str(a), str(b))
    dlg._add_paths([str(c)])
    dlg._set_baseline_from_paths([str(c)])
    assert Path(dlg.combo_baseline.currentData()).resolve() == c.resolve()
    assert dlg.lbl_all.text() == "全部："
    assert dlg.file_list.count() == 3
    dlg.close()


@pytest.mark.gui
def test_baseline_drop_event_is_handled(qapp, tmp_path: Path):
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent

    from engineerdiff.ui.batch_dialog import BatchCompareDialog

    a = tmp_path / "a.txt"; a.write_text("a\n", encoding="utf-8")
    c = tmp_path / "c.txt"; c.write_text("c\n", encoding="utf-8")
    dlg = BatchCompareDialog(None, AppSettings(), str(a))
    dlg.show()
    qapp.processEvents()
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(c))])
    event = QDropEvent(QPointF(12, 12), Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    assert dlg.baseline_zone.eventFilter(dlg.combo_baseline, event) is True
    assert dlg.file_list.count() == 2
    dlg.close()
