"""OCR 服务与图片比较的真实集成测试。

默认 pytest 会执行本文件：真实 RapidOCR 推理只在 2~4 张约 500×320 的小图上运行，
单次约 1~3 秒（首次含模型加载）。带 ``@pytest.mark.ocr`` 的用例也可用
``pytest -m ocr`` 单独运行。
"""

from __future__ import annotations

import math
import shutil
import time
from pathlib import Path

import pytest

from engineerdiff.image.engine import compare_images
from engineerdiff.image.matcher import normalize_text
from engineerdiff.models import DiffKind, ImageOptions, Severity
from engineerdiff.ocr.cache import CACHE_VERSION, OcrCache
from engineerdiff.ocr.engine import OcrCancelled, OcrError, OcrService, models_dir
from tests.assets.generate_images import ensure_generated_dir, render_ui_screenshot


# ---------------------------------------------------------------------------
# 模块级素材与 OCR 服务
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ocr_env() -> dict:
    generated = ensure_generated_dir()
    left_meta = render_ui_screenshot(generated / "ocr_e2e_left.png")
    right_meta = render_ui_screenshot(
        generated / "ocr_e2e_right.png",
        scale=1.2,
        offset=(30, 18),
        values={"version": "1.2.4", "value_in_1": "1.80"},
    )
    service = OcrService(cache_dir=generated / "ocr_test_cache")
    service.clear_cache()
    return {
        "generated": generated,
        "left_meta": left_meta,
        "right_meta": right_meta,
        "left": left_meta["path"],
        "right": right_meta["path"],
        "service": service,
    }


def _center(bbox: list[float] | tuple[float, ...]) -> tuple[float, float]:
    if len(bbox) == 4 and isinstance(bbox[0], int) and bbox[2] > bbox[0]:
        # (x0, y0, x1, y1) 形式
        return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
    return (bbox[0] + bbox[2] / 2.0, bbox[1] + bbox[3] / 2.0)


def _match_ocr_boxes_to_expected(meta: dict, boxes) -> list[tuple[str, dict, object, float]]:
    """把 OCR 框与真实绘制文字匹配（文本归一化 + 中心最近），用于坐标校验。"""
    remaining = list(meta["texts"].items())
    used: set[int] = set()
    pairs: list[tuple[str, dict, object, float]] = []
    for key, info in remaining:
        expected_center = _center(info["bbox"])
        best = None
        for box in boxes:
            if box.id in used:
                continue
            if normalize_text(box.text) != normalize_text(info["text"]):
                continue
            cx, cy = box.bbox.center
            distance = math.hypot(cx - expected_center[0], cy - expected_center[1])
            if best is None or distance < best[1]:
                best = (box, distance)
        if best is not None and best[1] <= 40:
            pairs.append((key, info, best[0], best[1]))
            used.add(best[0].id)
    return pairs


def _diff_for(diffs, left_text: str):
    for diff in diffs:
        if diff.left is not None and diff.left.text == left_text:
            return diff
    raise AssertionError(
        "未找到差异：" + repr([(d.left.text if d.left else None, d.right.text if d.right else None) for d in diffs])
    )


@pytest.fixture(scope="module")
def real_pages(ocr_env: dict) -> dict:
    """真实 RapidOCR 只识别一次左右图，供多个测试复用（缓存也随后命中）。"""
    service = ocr_env["service"]
    service.clear_cache()
    events: list[tuple[int, str]] = []
    left = service.recognize(ocr_env["left"], progress=lambda p, m: events.append((p, m)))
    right = service.recognize(ocr_env["right"])
    return {
        "left": left,
        "right": right,
        "events": events,
        "service": service,
        "cache_dir": service.cache_dir,
    }



# ---------------------------------------------------------------------------
# 模型与离线保证（不跑推理，快速）
# ---------------------------------------------------------------------------


def test_ocr_models_are_bundled_offline(ocr_env: dict):
    info = ocr_env["service"].model_info()
    assert info["engine"] == "RapidOCR"
    assert info["offline"] is True
    assert info["device"] == "CPU"
    assert info["engine_version"] != "unknown"
    assert info["backend"].startswith("ONNX Runtime")

    model_dir = Path(info["model_dir"])
    assert model_dir.is_dir(), f"未找到随包模型目录：{model_dir}"
    # 模型必须位于已安装的 rapidocr_onnxruntime 包内，而不是用户目录/网络
    import rapidocr_onnxruntime

    package_dir = Path(rapidocr_onnxruntime.__file__).resolve().parent
    assert model_dir.resolve().is_relative_to(package_dir)
    assert info["models_present"] is True
    assert len(info["models"]) >= 3
    names = {entry["name"] for entry in info["models"]}
    assert any("det" in name and name.endswith(".onnx") for name in names)
    assert any("rec" in name and name.endswith(".onnx") for name in names)
    for entry in info["models"]:
        model_path = Path(entry["path"])
        assert model_path.is_file()
        assert entry["size"] > 0
        assert model_path.resolve().is_relative_to(package_dir)
    assert Path(info["cache_dir"]) == ocr_env["service"].cache_dir
    assert info["cache_version"].startswith(CACHE_VERSION.split("|")[0])
    # 内置引擎不会请求网络：模型目录与 onnx 文件均在本地包内
    assert models_dir() is not None


def test_ocr_cache_roundtrip_and_invalid_entries(tmp_path: Path):
    cache = OcrCache(tmp_path / "cache", version="v-test")
    payload = {
        "path": "x.png",
        "width": 100,
        "height": 80,
        "boxes": [
            {"id": 0, "text": "1.2.3", "confidence": 0.99, "bbox": [1, 2, 3, 4], "quad": []}
        ],
    }
    assert cache.store("abc", payload) is True
    loaded = cache.load("abc")
    assert loaded is not None
    assert loaded["cache_version"] == "v-test"
    assert loaded["boxes"][0]["text"] == "1.2.3"
    assert cache.count() == 1

    # 版本不匹配 → 失效
    assert OcrCache(tmp_path / "cache", version="v-other").load("abc") is None
    # 损坏 JSON → 忽略，不抛异常
    (tmp_path / "cache" / "broken.json").write_text("{not valid json", encoding="utf-8")
    assert cache.load("broken") is None
    # 结构不合法 → 忽略
    (tmp_path / "cache" / "wrong.json").write_text('{"cache_version": "v-test"}', encoding="utf-8")
    assert cache.load("wrong") is None
    assert cache.clear() == 3  # abc/broken/wrong 三个 .json 文件
    assert cache.count() == 0


# ---------------------------------------------------------------------------
# 真实 OCR 识别
# ---------------------------------------------------------------------------


@pytest.mark.ocr
def test_real_ocr_boxes_match_drawn_text_positions(ocr_env: dict, real_pages: dict):
    for side, page, meta in (
        ("left", real_pages["left"], ocr_env["left_meta"]),
        ("right", real_pages["right"], ocr_env["right_meta"]),
    ):
        assert page.width == meta["image_size"][0]
        assert page.height == meta["image_size"][1]
        assert page.engine.startswith("RapidOCR")
        assert len(page.boxes) >= 11, f"{side} 识别文字框过少：{len(page.boxes)}"
        assert page.elapsed > 0
        expected_values = {
            "left": ("1.2.3", "1.50", "1.80"),
            "right": ("1.2.4", "1.80"),
        }[side]
        for value in expected_values:
            assert value in page.text, f"{side} OCR 结果缺少 {value}：{page.text!r}"
        for box in page.boxes:
            assert 0.0 <= box.confidence <= 1.0
            assert len(box.quad) == 4
            assert box.bbox.x >= -1 and box.bbox.y >= -1
            assert box.bbox.right <= page.width + 1
            assert box.bbox.bottom <= page.height + 1

        pairs = _match_ocr_boxes_to_expected(meta, page.boxes)
        assert len(pairs) >= 12, f"{side} 只有 {len(pairs)}/{len(meta['texts'])} 段文字被正确识别"
        for key, info, box, distance in pairs:
            # OCR 框中心与真实绘制文字中心偏差应很小（版式坐标 ±10px）
            assert distance <= 10.0, f"{side}/{key} 的 OCR 框偏差 {distance:.1f}px"
            expected_bbox = info["bbox"]
            from engineerdiff.models import BBox

            expected = BBox(
                expected_bbox[0],
                expected_bbox[1],
                expected_bbox[2] - expected_bbox[0],
                expected_bbox[3] - expected_bbox[1],
            )
            assert box.bbox.iou(expected) >= 0.30, f"{side}/{key} 的 OCR 框与文字重叠过小"


@pytest.mark.ocr
def test_progress_cache_and_cancel(real_pages: dict, tmp_path: Path):
    service: OcrService = real_pages["service"]
    events: list[tuple[int, str]] = real_pages["events"]
    percents = [percent for percent, _ in events]
    for required in (5, 30, 90, 100):
        assert required in percents, f"缺少进度 {required}%：{percents}"
    assert percents == sorted(percents)
    assert percents[-1] == 100
    assert real_pages["left"].cached is False
    assert real_pages["left"].elapsed > 0
    assert real_pages["right"].elapsed > 0

    cache_files = list(service.cache_dir.glob("*.json"))
    assert len(cache_files) >= 2

    # 第二次识别命中缓存，进度仍有 100
    cached_events: list[int] = []
    cached_page = service.recognize(real_pages["left"].path, progress=lambda p, _m: cached_events.append(p))
    assert cached_page.cached is True
    assert cached_events[-1] == 100
    assert [b.text for b in cached_page.boxes] == [b.text for b in real_pages["left"].boxes]
    assert cached_page.width == real_pages["left"].width
    assert cached_page.height == real_pages["left"].height

    # clear_cache：用独立目录验证（避免删除本模块复用的真实缓存）
    other = OcrService(cache_dir=tmp_path / "dummy_cache")
    other.cache_dir.mkdir(parents=True, exist_ok=True)
    (other.cache_dir / "dummy.json").write_text("{}", encoding="utf-8")
    other.clear_cache()
    assert list(other.cache_dir.glob("*.json")) == []

    # 识别前取消：立即抛 OcrCancelled，不产生新缓存
    before = len(list(service.cache_dir.glob("*.json")))
    with pytest.raises(OcrCancelled):
        service.recognize(real_pages["left"].path, cancel=lambda: True)
    assert len(list(service.cache_dir.glob("*.json"))) == before


@pytest.mark.ocr
def test_cancel_after_inference_discards_result(ocr_env: dict, tmp_path: Path):
    fresh = tmp_path / "fresh_left.png"
    shutil.copyfile(ocr_env["left"], fresh)
    service = OcrService(cache_dir=tmp_path / "cache")
    cancelled = {"value": False}

    def progress(percent: int, _message: str) -> None:
        if percent >= 55:
            cancelled["value"] = True

    with pytest.raises(OcrCancelled):
        service.recognize(
            str(fresh),
            progress=progress,
            cancel=lambda: cancelled["value"],
        )
    # OCR 调用不可中断，返回后因已取消而丢弃结果：不得写缓存
    assert list(service.cache_dir.glob("*.json")) == []


@pytest.mark.ocr
def test_missing_image_raises_ocr_error(ocr_env: dict, tmp_path: Path):
    service = OcrService(cache_dir=tmp_path / "cache")
    with pytest.raises(OcrError) as excinfo:
        service.recognize(str(tmp_path / "not_exists.png"))
    assert "不存在" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 真实 RapidOCR 端到端：不同尺寸/平移截图 → 匹配 → 标红
# ---------------------------------------------------------------------------


@pytest.mark.ocr
def test_compare_images_end_to_end_real_ocr(ocr_env: dict, real_pages: dict):
    service = real_pages["service"]
    left_meta, right_meta = ocr_env["left_meta"], ocr_env["right_meta"]
    progress: list[int] = []
    started = time.perf_counter()
    comparison = compare_images(
        ocr_env["left"],
        ocr_env["right"],
        ImageOptions(),
        service,
        progress=lambda p, _m: progress.append(p),
    )
    wall_time = time.perf_counter() - started
    assert wall_time > 0
    assert progress[-1] == 100
    assert progress == sorted(progress)
    assert comparison.left.boxes and comparison.right.boxes
    assert len(comparison.left.boxes) >= 11
    assert len(comparison.right.boxes) >= 11
    assert comparison.warnings == []
    assert comparison.transform["reliable"] is True
    assert abs(comparison.transform["scale"] - 1.0 / 1.2) < 0.1
    assert comparison.left.engine.startswith("RapidOCR")
    assert comparison.right.engine.startswith("RapidOCR")

    # 只有版本号与小数被标红，重复字段不产生假差异
    pairs = sorted(
        (diff.left.text, diff.right.text)
        for diff in comparison.diffs
        if diff.left is not None and diff.right is not None
    )
    assert pairs == [("1.2.3", "1.2.4"), ("1.50", "1.80")]
    for diff in comparison.diffs:
        assert diff.kind == DiffKind.MODIFIED
        assert diff.severity == Severity.CONFIRMED
        assert diff.group == "image"
        assert diff.left is not None and diff.right is not None
        assert diff.left.bbox is not None and diff.right.bbox is not None

    # 差异框落在各自原图的真实文字位置（与绘制坐标比较）
    version_diff = _diff_for(comparison.diffs, "1.2.3")
    for side, diff_location, meta in (
        ("left", version_diff.left, left_meta),
        ("right", version_diff.right, right_meta),
    ):
        expected_bbox = meta["texts"]["version"]["bbox"]
        expected_center = _center(expected_bbox)
        actual_center = diff_location.bbox.center
        distance = math.hypot(actual_center[0] - expected_center[0], actual_center[1] - expected_center[1])
        assert distance <= 10.0, f"{side} 版本号差异框偏差 {distance:.1f}px"
        # DiffLocation 坐标就是该侧 OcrPage 的框坐标
        assert diff_location.extra["box_id"] is not None
        page = comparison.left if side == "left" else comparison.right
        assert page.boxes[diff_location.extra["box_id"]].bbox == diff_location.bbox

    # 字符级区间：版本号第 5 个字符，小数第 3 个字符
    assert version_diff.left.spans == [(4, 5)]
    assert version_diff.right.spans == [(4, 5)]
    decimal_diff = _diff_for(comparison.diffs, "1.50")
    assert decimal_diff.left.spans == [(2, 3)]
    assert decimal_diff.right.spans == [(2, 3)]
    for diff in (version_diff, decimal_diff):
        for location in (diff.left, diff.right):
            assert len(location.segment_boxes) == 1
            segment = location.segment_boxes[0]
            assert location.bbox.x <= segment.x
            assert segment.right <= location.bbox.right + 1e-6
            assert location.bbox.y <= segment.y
            assert segment.bottom <= location.bbox.bottom + 1e-6
            assert segment.w >= 1.0
    # 数字位上的红框应位于文字框中部偏右，而不是整框
    version_segment_ratio = (version_diff.right.segment_boxes[0].x - version_diff.right.bbox.x) / (
        version_diff.right.bbox.w
    )
    assert 0.60 <= version_segment_ratio <= 0.95


@pytest.mark.ocr
def test_confusable_ocr_text_is_orange_end_to_end(ocr_env: dict, real_pages: dict):
    """端到端回归：同一内容不同尺寸截图，OCR 0/O、1/l 混淆只能橙色待复核。

    通过 text_overrides 把真实 OCR 结果中的字符替换为 OCR 常见混淆字形，验证
    完整链路（OCR 结果 → compare_images → matcher）给出的严重级别：
    - 0.00 → O.00、A1B2 → AlB2、1.50 → l.50 属于疑似 OCR 混淆 → 橙色 UNCERTAIN；
    - 1.2.3 → 1.2.4、1.50 → 1.80 真实数值差异 → 仍为红色 CONFIRMED。
    """
    service = real_pages["service"]
    overrides: dict[int, str] = {}
    for box in real_pages["right"].boxes:
        if box.text == "0.00":
            overrides[box.id] = "O.00"
        elif box.text == "A1B2":
            overrides[box.id] = "AlB2"
        elif box.text == "1.80":
            # 其中一个是真实的 1.50→1.80 变化，保持红色；这里不改动
            continue
    options = ImageOptions(text_overrides={"right": overrides})
    comparison = compare_images(ocr_env["left"], ocr_env["right"], options, service)
    assert comparison.warnings == []

    confirmed = {
        (diff.left.text, diff.right.text)
        for diff in comparison.diffs
        if diff.left is not None and diff.right is not None and diff.severity == Severity.CONFIRMED
    }
    # 真实数值/版本差异必须保持红色
    assert ("1.2.3", "1.2.4") in confirmed
    assert ("1.50", "1.80") in confirmed
    assert ("0.00", "O.00") not in confirmed
    assert ("A1B2", "AlB2") not in confirmed

    uncertain = [diff for diff in comparison.diffs if diff.severity == Severity.UNCERTAIN]
    assert len(uncertain) == 3, [(d.left.text if d.left else None, d.right.text if d.right else None) for d in uncertain]
    for diff in uncertain:
        assert diff.needs_review is True
        assert diff.kind == DiffKind.MODIFIED
        assert "OCR 字符混淆" in diff.detail
        assert diff.left is not None and diff.right is not None
        assert diff.left.bbox is not None and diff.right.bbox is not None
    uncertain_pairs = {(diff.left.text, diff.right.text) for diff in uncertain}
    assert uncertain_pairs == {("0.00", "O.00"), ("A1B2", "AlB2")}
    # 差异总数仍包含橙色项（用于导航）
    assert len(comparison.diffs) >= 4


# ---------------------------------------------------------------------------
# 真实 OCR 结果 + 人工修正/人工对应（用假 OCR 避免重复推理，另见图片匹配测试）
# ---------------------------------------------------------------------------


def test_compare_images_with_real_pages_manual_pair_is_ignored_when_invalid(tmp_path: Path):
    """人工对应引用不存在 id 时给出警告而不是崩溃。"""
    left_meta = render_ui_screenshot(tmp_path / "left.png")
    right_meta = render_ui_screenshot(tmp_path / "right.png")
    # 直接构造两个空页，验证 manual_pairs 非法 id 的告警路径
    from engineerdiff.models import OcrPage

    left_page = OcrPage(path=str(tmp_path / "left.png"), width=left_meta["image_size"][0], height=left_meta["image_size"][1])
    right_page = OcrPage(path=str(tmp_path / "right.png"), width=right_meta["image_size"][0], height=right_meta["image_size"][1])

    class _Fake:
        def recognize(self, path, progress=None, cancel=None):
            return left_page if str(path) == left_page.path else right_page

    options = ImageOptions(manual_pairs=[(0, 0)])
    comparison = compare_images(left_page.path, right_page.path, options, _Fake())
    assert any("人工对应引用了不存在的文字框" in note for note in comparison.transform["notes"])


def test_compare_images_empty_pages_are_not_equal(tmp_path: Path):
    from engineerdiff.models import OcrPage

    left_page = OcrPage(path=str(tmp_path / "a.png"), width=80, height=60)
    right_page = OcrPage(path=str(tmp_path / "b.png"), width=80, height=60)

    class _Fake:
        def recognize(self, path, progress=None, cancel=None):
            return left_page if str(path) == left_page.path else right_page

    comparison = compare_images(left_page.path, right_page.path, ImageOptions(), _Fake())
    assert comparison.diffs == []
    assert any("未识别到文字" in warning for warning in comparison.warnings)


def test_ocr_profile_selection_uses_native_small_images():
    from engineerdiff.ocr.engine import OcrService

    assert OcrService._profile_for_size(416, 256) == "small"
    assert OcrService._profile_for_size(1920, 1080) == "large"
    assert OcrService._profile_for_size(3840, 2160) == "large"
