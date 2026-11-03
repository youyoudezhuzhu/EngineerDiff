#!/usr/bin/env python3
"""校验 PyInstaller one-dir 产物是否完整，并运行打包后的 `--self-test`。

用法：
    python scripts/verify_bundle.py --dist dist/EngineerDiff \
        --exe dist/EngineerDiff/EngineerDiff.exe --require-frozen

校验项：
* exe 存在且可执行；
* RapidOCR 的 3 个 ONNX 模型 + config.yaml 位于包内（离线 OCR 的关键）；
* ONNX Runtime 动态库、OpenCV 动态库、Qt 核心库/平台插件/图片格式插件齐全；
* 应用图标资源存在；
* LICENSE、THIRD-PARTY-NOTICES.md、MODEL_LICENSES.md、LICENSES/MANIFEST.json 齐全；
* 运行 `EngineerDiff.exe --self-test --self-test-report <path>`：
  退出码 0、报告存在、`ok=true`、所有 check 通过、`frozen=true`（证明跑的是打包产物）。

Linux 本地验证时自动设置 `QT_QPA_PLATFORM=offscreen`；Windows CI 原样运行。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_MODELS = {
    "ch_PP-OCRv4_det_infer.onnx",
    "ch_PP-OCRv4_rec_infer.onnx",
    "ch_ppocr_mobile_v2.0_cls_infer.onnx",
}


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            self.failures.append(f"{name}: {detail}")
        return ok

    def summary(self) -> int:
        print()
        total = len(self.checks)
        failed = len(self.failures)
        print(f"结果：{total - failed}/{total} 项通过")
        if failed:
            print("失败项：")
            for item in self.failures:
                print(f"  - {item}")
            return 1
        return 0


def project_version() -> str:
    ns: dict = {}
    version_py = PROJECT_ROOT / "src" / "engineerdiff" / "version.py"
    exec(compile(version_py.read_text(encoding="utf-8"), str(version_py), "exec"), ns)
    return str(ns.get("__version__", ""))


def first_glob(root: Path, *patterns: str) -> Path | None:
    for pattern in patterns:
        matches = sorted(root.glob(pattern))
        if matches:
            return matches[0]
    return None


def check_models(c: Checker, dist: Path) -> None:
    model_dir = first_glob(dist, "**/rapidocr_onnxruntime/models")
    if model_dir is None:
        c.check("OCR 模型目录", False, "未找到 rapidocr_onnxruntime/models")
        return
    c.check("OCR 模型目录", True, str(model_dir.relative_to(dist)))
    models = {p.name for p in model_dir.glob("*.onnx")}
    missing = EXPECTED_MODELS - models
    c.check(
        "OCR 3 个 ONNX 模型",
        not missing,
        f"发现 {sorted(models)}" + (f"，缺少 {sorted(missing)}" if missing else ""),
    )
    sizes = {p.name: p.stat().st_size for p in model_dir.glob("*.onnx")}
    tiny = [name for name, size in sizes.items() if size < 100_000]
    c.check("OCR 模型非空", not tiny, f"大小：{sizes}" + (f"；可疑过小：{tiny}" if tiny else ""))
    config = model_dir.parent / "config.yaml"
    c.check("RapidOCR config.yaml", config.is_file(), str(config.relative_to(dist)) if config.is_file() else "缺失")


def check_binaries(c: Checker, dist: Path) -> None:
    all_files = [p for p in dist.rglob("*") if p.is_file()]
    names = {p.name.lower() for p in all_files}

    ort = [p for p in all_files if p.name.lower().startswith(("onnxruntime", "libonnxruntime"))]
    c.check("ONNX Runtime 动态库", bool(ort), f"{[p.name for p in ort]}")

    cv = [
        p
        for p in all_files
        if p.name.lower().startswith(("cv2.", "opencv_videoio", "libopencv"))
    ]
    c.check("OpenCV 动态库/模块", bool(cv), f"{[p.name for p in cv][:8]}")

    if sys.platform == "win32":
        qt_core = names & {"qt6core.dll", "qt6gui.dll", "qt6widgets.dll"}
        c.check("Qt 核心 DLL", len(qt_core) == 3, f"{sorted(qt_core)}")
        platform = first_glob(dist, "**/plugins/platforms/qwindows.dll")
        c.check("Qt 平台插件 qwindows.dll", platform is not None, str(platform) if platform else "缺失")

        # 干净 Windows 可能没有 VC++ 运行库；安装包必须自带 ONNX Runtime/OpenCV 所需 DLL
        vc_required = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
        vc_present = [name for name in vc_required if name in names]
        c.check(
            "VC++ 运行库 DLL（msvcp140/vcruntime140/vcruntime140_1）",
            len(vc_present) == len(vc_required),
            f"{vc_present}" + (f"，缺少 {sorted(set(vc_required) - set(vc_present))}" if len(vc_present) < len(vc_required) else ""),
        )
    else:
        qt_core = [p for p in all_files if p.name.startswith(("libQt6Core.so", "libQt6Gui.so", "libQt6Widgets.so"))]
        c.check("Qt 核心库", len(qt_core) >= 3, f"{[p.name for p in qt_core][:5]}")
        platform = first_glob(dist, "**/plugins/platforms/libqxcb.so", "**/plugins/platforms/*.so")
        c.check("Qt 平台插件", platform is not None, str(platform) if platform else "缺失")

    image_plugins = first_glob(dist, "**/plugins/imageformats")
    if image_plugins is not None:
        jpeg = list(image_plugins.glob("*jpeg*"))
        c.check(
            "Qt JPEG 图片插件",
            bool(jpeg),
            f"{[p.name for p in jpeg]}（共 {len(list(image_plugins.iterdir()))} 个图片格式插件）",
        )
    else:
        c.check("Qt imageformats 插件", False, "未找到 plugins/imageformats")

    styles = first_glob(dist, "**/plugins/styles")
    if sys.platform == "win32":
        # Windows 轮子带 qmodernwindowsstyle.dll；缺失会导致原生外观回退，视为失败
        c.check("Qt 风格插件", styles is not None, str(styles) if styles else "缺失（Windows 包不应缺失）")
    else:
        # Linux 的 PySide6 wheel 不提供 styles 插件目录（Fusion 风格内置），仅提示
        print(f"[INFO] Qt 风格插件：{styles if styles else 'Linux wheel 不含 styles 插件（预期，Fusion 内置）'}")

    icon = first_glob(dist, "**/engineerdiff/resources/engineerdiff.ico")
    c.check("应用图标资源", icon is not None, str(icon.relative_to(dist)) if icon else "缺失")


def check_docs(c: Checker, dist: Path) -> None:
    for rel in (
        "LICENSE",
        "THIRD-PARTY-NOTICES.md",
        "MODEL_LICENSES.md",
        "LICENSES/MANIFEST.json",
        "docs/user-guide.md",
        "docs/known-limitations.md",
        "docs/third-party-licenses.md",
        "docs/windows-defender.md",
        "docs/code-signing.md",
        "docs/ocr-performance.md",
        "docs/screenshots/text-compare.png",
        "docs/screenshots/dark-mode.png",
        "docs/screenshots/batch-compare.png",
        "docs/screenshots/batch-excel.png",
        "docs/screenshots/batch-image-view.png",
        "docs/screenshots/about.png",
        "docs/screenshots/engineerdiff-icon.png",
    ):
        path = dist / rel
        c.check(f"交付文件 {rel}", path.is_file() and path.stat().st_size > 0, "存在" if path.is_file() else "缺失")


def run_self_test(
    c: Checker,
    exe: Path,
    dist: Path,
    timeout: int,
    require_frozen: bool,
    report_path: Path,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists():
        report_path.unlink()
    env = os.environ.copy()
    if sys.platform != "win32":
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    cmd = [str(exe), "--self-test", "--self-test-report", str(report_path)]
    print(f"\n运行自检：{' '.join(cmd)}  (cwd={dist})")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(dist),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        c.check("打包产物 --self-test 执行", False, f"超时（>{timeout}s）")
        return
    except OSError as exc:
        c.check("打包产物 --self-test 执行", False, f"无法启动：{exc}")
        return

    if proc.stdout:
        print("--- stdout ---")
        print(proc.stdout[-8000:])
    if proc.stderr:
        print("--- stderr ---")
        print(proc.stderr[-8000:])

    c.check("打包产物 --self-test 退出码", proc.returncode == 0, f"exit={proc.returncode}")
    if not report_path.is_file():
        c.check("self-test 报告文件", False, f"未生成 {report_path}")
        return
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        c.check("self-test 报告可解析", False, f"{type(exc).__name__}: {exc}")
        return
    c.check("self-test 报告可解析", True, str(report_path))
    c.check("self-test 报告 ok=true", report.get("ok") is True, json.dumps(report.get("error", ""), ensure_ascii=False))
    checks = report.get("checks") or []
    failed = [item for item in checks if not item.get("ok")]
    c.check("self-test 各检查项通过", bool(checks) and not failed, f"{len(checks)} 项" + (f"，失败 {failed}" if failed else ""))
    version = project_version()
    c.check("self-test 版本一致", report.get("version") == version, f"报告 {report.get('version')} / 仓库 {version}")
    if require_frozen:
        c.check("self-test 运行于打包产物（frozen）", report.get("frozen") is True, f"frozen={report.get('frozen')}")
    for item in checks:
        print(f"    - {item.get('name')}: {'ok' if item.get('ok') else 'FAIL'} ({item.get('detail', '')})")


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 EngineerDiff PyInstaller one-dir 产物")
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--exe", type=Path, default=None)
    parser.add_argument("--timeout", type=int, default=600, help="self-test 超时秒数")
    parser.add_argument("--skip-self-test", action="store_true")
    parser.add_argument("--require-frozen", action="store_true", help="要求报告 frozen=true")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument(
        "--self-test-report-output",
        type=Path,
        default=None,
        help="把 self-test 报告写到指定路径（便于 CI 检查；默认写入临时目录）",
    )
    args = parser.parse_args()

    dist = args.dist.resolve()
    exe = args.exe or (dist / ("EngineerDiff.exe" if sys.platform == "win32" else "EngineerDiff"))
    exe = exe.resolve()

    c = Checker()
    c.check("dist 目录存在", dist.is_dir(), str(dist))
    c.check("EngineerDiff 可执行文件", exe.is_file(), str(exe))
    if not dist.is_dir() or not exe.is_file():
        return c.summary()

    check_models(c, dist)
    check_binaries(c, dist)
    check_docs(c, dist)
    if not args.skip_self_test:
        report_path = args.self_test_report_output
        if report_path is None:
            report_path = Path(tempfile.mkdtemp(prefix="engineerdiff-verify-")) / "self-test-report.json"
        else:
            report_path = report_path.resolve()
        run_self_test(c, exe, dist, args.timeout, args.require_frozen, report_path)

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps({"dist": str(dist), "exe": str(exe), "checks": c.checks}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return c.summary()


if __name__ == "__main__":
    raise SystemExit(main())
