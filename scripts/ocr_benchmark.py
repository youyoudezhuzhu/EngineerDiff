"""EngineerDiff OCR 性能基准。

用法：
    python scripts/ocr_benchmark.py --label before --json docs/ocr-benchmark-before.json

数据集在 build/ocr-bench/ 自动生成（不入库）：
- 1080p / 4K 单图
- 10 张不同截图
- 10 张重复截图
- 小字密集截图
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from engineerdiff.models import OcrPage  # noqa: E402
from engineerdiff.ocr.engine import OcrService  # noqa: E402


def _rss_mb() -> float:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return 0.0


def _prepare() -> dict[str, Path]:
    root = ROOT / "build" / "ocr-bench"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    from tests.assets.generate_images import render_ui_screenshot

    base = Path(render_ui_screenshot(root / "base.png")["path"])
    images = {}
    # 1080p：放大到 1920x1080（保持文字位置/比例）
    for name, size in (("1080p", (1920, 1080)), ("4k", (3840, 2160))):
        im = Image.open(base).convert("RGB").resize(size, Image.Resampling.LANCZOS)
        out = root / f"{name}.png"
        im.save(out)
        images[name] = out
    # 10 张不同截图
    variants = []
    for i in range(10):
        values = {
            "version": f"1.2.{i}",
            "value_in_1": f"{1.5 + i / 100:.2f}",
            "check": f"A1B{i}",
        }
        variants.append(Path(render_ui_screenshot(root / f"distinct_{i}.png", values=values)["path"]))
    images["distinct"] = variants
    # 10 张重复
    dup = root / "duplicate.png"
    shutil.copyfile(variants[0], dup)
    images["duplicates"] = [dup] * 10
    # 小字密集：1600x1200，40 行参数
    dense = root / "dense.png"
    im = Image.new("RGB", (1600, 1200), "white")
    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype(str(ROOT / "tests" / "assets" / "NotoSansSC-Regular.otf"), 18)
    except Exception:
        font = ImageFont.load_default()
    for i in range(40):
        draw.text((40, 20 + i * 28), f"定值{i:02d}  投入 {i / 100:.2f}  退出 {i / 50:.2f}  正常", font=font, fill=(20, 20, 20))
    im.save(dense)
    images["dense"] = dense
    images["root"] = root
    return images


def _run_case(name: str, paths: list[Path], cache: Path, warmup: bool = True) -> dict:
    svc = OcrService(cache_dir=cache)
    t0 = time.perf_counter()
    if warmup:
        svc.warmup()
    model_load = time.perf_counter() - t0
    times = []
    pages = []
    for path in paths:
        t = time.perf_counter()
        page = svc.recognize(str(path))
        times.append(time.perf_counter() - t)
        pages.append(page)
    total = sum(times)
    return {
        "case": name,
        "files": len(paths),
        "model_load_s": round(model_load, 3),
        "first_s": round(times[0], 3) if times else 0,
        "total_s": round(total, 3),
        "mean_s": round(statistics.mean(times), 3) if times else 0,
        "p95_s": round(sorted(times)[max(0, int(len(times) * 0.95) - 1)], 3) if times else 0,
        "text_boxes": sum(len(p.boxes) for p in pages),
        "rss_mb": round(_rss_mb(), 1),
        "cache_entries": svc.model_info()["cache_entries"],
        "cached_pages": sum(1 for p in pages if p.cached),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    imgs = _prepare()
    root = imgs["root"]
    cases = []
    cases.append(_run_case("1080p_single", [imgs["1080p"]], root / "cache_1080"))
    cases.append(_run_case("4k_single", [imgs["4k"]], root / "cache_4k"))
    cases.append(_run_case("distinct_10", imgs["distinct"], root / "cache_distinct"))
    cases.append(_run_case("duplicates_10", imgs["duplicates"], root / "cache_duplicates"))
    cases.append(_run_case("dense_small", [imgs["dense"]], root / "cache_dense"))
    report = {
        "label": args.label,
        "version": __import__("engineerdiff.version", fromlist=["__version__"]).__version__,
        "cpu_count": os.cpu_count(),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cases": cases,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
