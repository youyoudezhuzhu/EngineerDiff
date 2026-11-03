# packaging 目录说明

本目录只放**构建/打包配置**，不放业务源码。产物路径约定见下，构建日志与产物
（`build/`、`dist/`、`release/`）一律不提交 git。

## 文件

| 文件 | 作用 |
|---|---|
| `engineerdiff.spec` | PyInstaller **one-dir** 打包脚本（窗口版 `EngineerDiff.exe`，`_internal/` 布局） |
| `launcher.py` | PyInstaller 入口脚本，仅调用 `engineerdiff.app:main`（符合入口契约） |
| `version_info.txt` | Windows exe 版本资源（版本号需与 `src/engineerdiff/version.py` 一致） |
| `engineerdiff.iss` | Inno Setup 6 安装包脚本（per-user、简体中文、离线、许可页、快捷方式、卸载） |
| `languages/ChineseSimplified.isl` | 简体中文安装界面语言文件（MIT，来源见 `languages/README.md`） |
| `licenses/` | wheel 未携带原文时使用的标准许可证文本（LGPL-3.0、GPL-3.0、Apache-2.0、MIT、BSD-3-Clause、PSF-2.0、MPL-2.0、Inno Setup 许可等） |

## 构建命令（Windows x64）

前置：Python 3.11 x64、`pip install -r requirements-dev.txt`、Inno Setup 6.5+
（`scripts/install_inno.ps1` 可自动安装）。

```powershell
# 一键构建（测试 -> PyInstaller -> 自检 -> 便携 zip -> 安装包 -> sha256）
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1

# 分步
python scripts\check_spec.py
python scripts\check_deps.py
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\engineerdiff.spec
python scripts\collect_licenses.py --dist dist\EngineerDiff --strict
python scripts\verify_bundle.py --dist dist\EngineerDiff --exe dist\EngineerDiff\EngineerDiff.exe --require-frozen --self-test-report-output build\self-test-report.json
ISCC.exe /Qp packaging\engineerdiff.iss
```

Linux 只能做依赖收集自检（产物不交付）：

```bash
scripts/build_linux_check.sh            # 完整（含 offscreen --self-test）
scripts/build_linux_check.sh --skip-self-test
```

## 产物与命名约定

```text
dist\EngineerDiff\EngineerDiff.exe                     PyInstaller one-dir（含 _internal\）
release\EngineerDiff-1.2.2-Setup-x64.exe               Inno Setup 安装包（默认 per-user）
release\EngineerDiff-1.2.2-portable-win64.zip          便携版 zip
release\sha256sums.txt                                 以上文件的 SHA-256
build\windows-build-summary.json                       构建摘要（版本/ISCC/大小/哈希）
build\self-test-report.json                            dist 产物自检报告
build\installed-self-test.json                         CI 安装包实测（安装后自检）报告
```

安装包默认安装到 `%LOCALAPPDATA%\Programs\EngineerDiff`（`PrivilegesRequired=lowest`，
无需管理员）。安装包内包含：exe + `_internal`（Qt 插件、OCR 模型、ONNX Runtime、
VC++ 运行库 DLL）、`docs\`（含 `screenshots\` 界面截图）、`LICENSES\`、
`THIRD-PARTY-NOTICES.md`、`MODEL_LICENSES.md`。

## spec 中的 Windows 专属项

以下逻辑在 Linux 本地构建时不会生效或不可验证，真实结果以 GitHub Actions
`windows-2022`（工作流 `.github/workflows/build-windows.yml`）为准：

1. `EXE(icon=src/engineerdiff/resources/engineerdiff.ico, version=packaging/version_info.txt)`
   仅在 `sys.platform == "win32"` 时生效；Linux 产物无图标/版本资源。
2. 显式收集 VC++ 运行库 DLL（`msvcp140*.dll`、`vcruntime140*.dll`、`concrt140.dll`、
   `vcomp140.dll`，来自 `%SystemRoot%\System32`），并要求 `msvcp140.dll`、
   `vcruntime140.dll`、`vcruntime140_1.dll` 必须存在，否则构建失败。
   ONNX Runtime / OpenCV 在干净 Windows 上依赖这些 DLL。
3. Qt 平台插件为 `qwindows.dll`（Linux 本地为 `libqxcb.so`），图片格式插件
   `qjpeg` 等仅 Windows 轮子齐全。
4. ONNX Runtime 为 `onnxruntime.dll` / `onnxruntime_providers_shared.dll`
   （Linux 为 `libonnxruntime.so*`）。
5. 安装包只能由 Windows 上的 `ISCC.exe` 生成；CI 还会静默安装 → 安装后
   `--self-test` → 静默卸载做端到端验证。
6. 窗口版 exe（`console=False`）的退出码/报告行为需在 Windows 上复核。

## 环境变量

| 变量 | 用途 |
|---|---|
| `ENGINEERDIFF_LAUNCHER` | **仅** Linux 依赖收集自检时代替入口脚本，构建日志会打印醒目警告。正式构建/CI 不得设置。 |
| `ISCC_PATH` | 指定 `ISCC.exe` 路径（`scripts/find_iscc.ps1` 优先使用）。 |

## 版本升级清单

1. 修改 `src/engineerdiff/version.py` 的 `__version__`；
2. 同步 `pyproject.toml` 的 `version`、`packaging/version_info.txt` 的 `filevers/prodvers/
   FileVersion/ProductVersion`、`packaging/engineerdiff.iss` 的 `APP_VERSION`；
3. 运行 `python scripts/check_spec.py`（会自动校验版本一致性）；
4. 更新 `docs/third-party-licenses.md` 中版本表（如依赖变化）；
5. 打 tag `v<version>` 触发 Release 工作流（由维护者推送）。
