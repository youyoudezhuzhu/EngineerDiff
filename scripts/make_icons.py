#!/usr/bin/env python3
"""从资产图生成 EngineerDiff 圆角应用图标。

用法：
    python scripts/make_icons.py [--source assets/engineerdiff-icon-source.png]

产物：
    src/engineerdiff/resources/engineerdiff.ico  多尺寸 16..256
    src/engineerdiff/resources/engineerdiff.png  512x512
    docs/screenshots/engineerdiff-icon.png       512x512（项目介绍页）

所有尺寸使用同一圆角遮罩裁剪，透明角外不保留蓝色背景。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "assets" / "engineerdiff-icon-source.png"
RES = ROOT / "src" / "engineerdiff" / "resources"
DOCS = ROOT / "docs" / "screenshots"
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def rounded_logo(source: Path, size: int = 1024) -> Image.Image:
    image = Image.open(source).convert("RGBA")
    # 居中裁成正方形，缩放到统一工作尺寸
    side = min(image.width, image.height)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side)).resize(
        (size, size), Image.Resampling.LANCZOS
    )
    # 约 20% 圆角，符合 Windows 11 应用图标比例
    radius = int(size * 0.20)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    image.putalpha(mask)
    return image


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    args = ap.parse_args()
    source = Path(args.source)
    if not source.is_file():
        raise SystemExit(f"找不到图标源文件：{source}")
    base = rounded_logo(source, 1024)
    RES.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    png = base.resize((512, 512), Image.Resampling.LANCZOS)
    png.save(RES / "engineerdiff.png", "PNG", optimize=True)
    png.save(DOCS / "engineerdiff-icon.png", "PNG", optimize=True)
    # Pillow 的 ICO encoder 直接从高分辨率底图生成多尺寸帧；不要 append_images，
    # 否则只会写出第一帧 16x16。
    base.save(
        RES / "engineerdiff.ico",
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
    )
    print(f"icon source: {source}")
    print(f"written: {RES/'engineerdiff.png'} {RES/'engineerdiff.ico'} {DOCS/'engineerdiff-icon.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
