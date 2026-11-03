# -*- mode: python ; coding: utf-8 -*-
"""EngineerDiff 的 PyInstaller one-dir 打包脚本。

设计要点（与 task-4 契约一致）：

* one-dir（stable，不做单文件）：`dist/EngineerDiff/EngineerDiff.exe` +
  `dist/EngineerDiff/_internal/`，首次启动解压/加载更快，OCR/CV DLL 不易被
  杀软误伤，也便于排查缺失文件；
* 窗口版 exe：`console=False`，exe 名 `EngineerDiff.exe`，嵌入图标与 Windows
  版本资源；
* 必须真实收集：RapidOCR 的 3 个 ONNX 模型 + `config.yaml`、ONNX Runtime 动态
  库、OpenCV/numpy、openpyxl/xlrd、PySide6 Qt 平台/图片格式插件；
* 排除 WebEngine/Quick/Qml/3D/Multimedia 等未使用的 Qt 大模块，控制体积；
* 许可证与使用说明随包（同时由构建脚本复制到 dist 根目录，便于便携版阅读）。

本地（Linux）验证：`scripts/build_linux_check.sh` 会设置 `ENGINEERDIFF_LAUNCHER`
到临时桩脚本，仅用于验证依赖收集；正式产物只允许由默认入口（launcher.py →
engineerdiff.app:main）在 Windows CI 构建。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

PROJECT_ROOT = Path(SPECPATH).resolve().parent
PACKAGING_DIR = Path(SPECPATH).resolve()
SRC_DIR = PROJECT_ROOT / "src"
RESOURCES_DIR = SRC_DIR / "engineerdiff" / "resources"
ICON_FILE = RESOURCES_DIR / "engineerdiff.ico"
VERSION_FILE = PACKAGING_DIR / "version_info.txt"
DEFAULT_LAUNCHER = PACKAGING_DIR / "launcher.py"

# ---------------------------------------------------------------------------
# 入口：默认必须是 packaging/launcher.py（→ engineerdiff.app:main）。
# ENGINEERDIFF_LAUNCHER 仅供 Linux 依赖收集自检使用（见 scripts/build_linux_check.sh），
# CI 与正式发布不得设置该变量；设置了会在构建日志中打印醒目警告。
# ---------------------------------------------------------------------------
launcher = Path(os.environ.get("ENGINEERDIFF_LAUNCHER", str(DEFAULT_LAUNCHER))).resolve()
if not launcher.is_file():
    raise SystemExit(f"ERROR: 找不到 PyInstaller 入口脚本：{launcher}")
if launcher != DEFAULT_LAUNCHER.resolve():
    print(
        "WARNING: 使用了 ENGINEERDIFF_LAUNCHER 覆盖入口，"
        f"该产物仅用于依赖收集自检，不是正式发布入口：{launcher}",
        file=sys.stderr,
    )

# ---------------------------------------------------------------------------
# 数据文件
# ---------------------------------------------------------------------------
datas: list[tuple[str, str]] = []

# 1) RapidOCR：3 个 ONNX 模型 + config.yaml。RapidOCR 依据自身 __file__ 相对定位
#    config.yaml 与 models/，因此必须保持 rapidocr_onnxruntime/ 包内相对路径。
rapidocr_data = collect_data_files(
    "rapidocr_onnxruntime",
    includes=["config.yaml", "models/*.onnx"],
)
onnx_models = [src for src, _ in rapidocr_data if src.lower().endswith(".onnx")]
if len(onnx_models) < 3:
    raise SystemExit(
        "ERROR: RapidOCR 模型收集不完整，期望 3 个 .onnx，实际 "
        f"{len(onnx_models)} 个：{onnx_models}"
    )
datas += rapidocr_data

# 2) 应用图标/资源（app 运行时按 engineerdiff/resources 相对路径加载）
if RESOURCES_DIR.is_dir():
    datas.append((str(RESOURCES_DIR), "engineerdiff/resources"))

# 3) 许可证与中文交付文档（随包分发；安装包会再放到安装目录根下）
for _rel, _dest in (
    ("LICENSE", "."),
    ("README.md", "."),
    ("docs/user-guide.md", "docs"),
    ("docs/known-limitations.md", "docs"),
    ("docs/third-party-licenses.md", "docs"),
    ("docs/windows-defender.md", "docs"),
    ("docs/code-signing.md", "docs"),
    ("docs/ocr-performance.md", "docs"),
    ("docs/architecture.md", "docs"),
):
    _path = PROJECT_ROOT / _rel
    if _path.is_file():
        datas.append((str(_path), _dest))
_screenshots = PROJECT_ROOT / "docs" / "screenshots"
if _screenshots.is_dir():
    datas.append((str(_screenshots), "docs/screenshots"))

# ---------------------------------------------------------------------------
# 二进制依赖
# ---------------------------------------------------------------------------
binaries: list[tuple[str, str]] = []

# 1) ONNX Runtime 动态库（onnxruntime.dll / onnxruntime_providers_shared.dll 等）。
#    pyinstaller-hooks-contrib 自带 hook 也会收集；此处显式收集作为兜底，TOC 会去重。
binaries += collect_dynamic_libs("onnxruntime")

# 2) OpenCV 动态库（Windows 上主要是 opencv_videoio_ffmpeg*.dll）
binaries += collect_dynamic_libs("cv2")

# 3) PySide6 Qt 插件兜底：平台插件（qwindows / libqxcb）与图片格式插件（qjpeg 等）
#    是 Qt 能创建窗口、加载 JPG/ICO 的关键。PyInstaller 的 PySide6 hook 会根据
#    import 自动收集，这里再显式加入，避免 hook 行为变化导致 Windows 包缺插件。
try:
    import PySide6  # noqa: F401

    _qt_plugins = Path(PySide6.__file__).resolve().parent / "plugins"
    _qt_rel_dir = "PySide6" if sys.platform == "win32" else "PySide6/Qt"
    for _plugin_type in ("platforms", "imageformats", "iconengines", "styles", "tls"):
        _plugin_dir = _qt_plugins / _plugin_type
        if not _plugin_dir.is_dir():
            continue
        for _plugin in sorted(_plugin_dir.iterdir()):
            if not _plugin.is_file() or _plugin.name.lower().startswith("qsvg"):
                # qsvg 插件依赖 QtSvg，本应用不使用 SVG，跳过以免引入额外 DLL
                continue
            binaries.append((str(_plugin), f"{_qt_rel_dir}/plugins/{_plugin_type}"))
except ImportError as exc:  # pragma: no cover - PySide6 是硬依赖
    raise SystemExit(f"ERROR: 构建环境缺少 PySide6：{exc}") from exc

# 4) Windows：显式收集 Microsoft VC++ 运行库 DLL。
#    ONNX Runtime / OpenCV 的 Windows wheel 依赖 msvcp140.dll、vcruntime140.dll、
#    vcruntime140_1.dll、concrt140.dll 等；干净的 Windows 可能没有安装 VC++ 运行库。
#    按 Microsoft Visual C++ Redistributable 的再分发条款，可随应用分发这些 DLL
#    （见 docs/third-party-licenses.md）。缺失时让构建直接失败，避免产出“装完打不开”
#    的安装包。构建脚本还会额外复制一份到 dist 根目录，双保险。
if sys.platform == "win32":
    _system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    # 只允许再分发 release 版 VC++ 运行库；显式白名单避免把 msvcp140d*、
    # msvcp140_clr0400 等调试/CLR 专用 DLL 打进去（体积与许可风险）。
    _vc_allow = (
        "msvcp140.dll",
        "msvcp140_1.dll",
        "msvcp140_2.dll",
        "msvcp140_atomic_wait.dll",
        "msvcp140_codecvt_ids.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "concrt140.dll",
        "vcomp140.dll",
    )
    _vc_found: set[str] = set()
    for _name in _vc_allow:
        _dll = _system32 / _name
        if _dll.is_file():
            binaries.append((str(_dll), "."))
            _vc_found.add(_name.lower())
    _vc_required = {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
    _vc_missing = sorted(_vc_required - _vc_found)
    if _vc_missing:
        raise SystemExit(
            "ERROR: 构建机 System32 缺少 VC++ 运行库 DLL："
            f"{_vc_missing}；请先安装 Microsoft Visual C++ 2015-2022 Redistributable (x64)。"
        )
    print(f"INFO: 已收集 VC++ 运行库 DLL：{sorted(_vc_found)}")

# ---------------------------------------------------------------------------
# 隐藏导入
# ---------------------------------------------------------------------------
hiddenimports: list[str] = []
hiddenimports += collect_submodules("engineerdiff")
hiddenimports += collect_submodules("rapidocr_onnxruntime")
# 各依赖中按需/惰性导入的模块（PyInstaller 静态分析可能漏掉）
hiddenimports += [
    "openpyxl.cell._writer",
    "xlrd.compdoc",
]

# ---------------------------------------------------------------------------
# 排除项：未使用的 Qt 大模块与无关库
# ---------------------------------------------------------------------------
excludes = [
    "tkinter",
    "matplotlib",
    "pandas",
    "scipy",
    "IPython",
    "pytest",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtAxContainer",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtHelp",
    "PySide6.QtHttpServer",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
]

# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------
a = Analysis(
    [str(launcher)],
    pathex=[str(SRC_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EngineerDiff",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 窗口版
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_FILE) if sys.platform == "win32" and ICON_FILE.is_file() else None,
    version=str(VERSION_FILE) if sys.platform == "win32" and VERSION_FILE.is_file() else None,
    contents_directory="_internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EngineerDiff",
)
