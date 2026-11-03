"""内置离线 OCR 服务。

技术栈：``rapidocr-onnxruntime`` + ``onnxruntime``（CPU）。模型文件随
``rapidocr_onnxruntime`` wheel 一起分发在 ``rapidocr_onnxruntime/models`` 目录，
本模块不做任何网络访问，也不依赖外部 OCR 服务/Paddle。

设计要点：
- 延迟初始化 + 进程内共享单例引擎（首次识别时才加载模型）；
- 多线程串行保护（底层 ONNX Runtime 会话为共享资源）；
- 保留原图坐标（左上原点像素）、置信度与四点框；
- SHA-256 磁盘缓存，只缓存成功结果，版本变化自动失效；
- 进度回调与取消：OCR 调用本身不可中断，返回后如已取消则抛 ``OcrCancelled``。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import io
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from ..models import BBox, OcrBox, OcrPage
from .cache import CACHE_VERSION, OcrCache, default_cache_dir, sha256_file

ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]


class OcrError(RuntimeError):
    """OCR 初始化/识别失败（消息可直接展示给用户）。"""


class OcrCancelled(OcrError):
    """用户取消识别。调用方应静默处理，不作为错误弹窗。"""


# ---------------------------------------------------------------------------
# 进程内共享引擎（懒加载单例）
# ---------------------------------------------------------------------------

_ENGINE_LOCK = threading.RLock()
_INFER_LOCK = threading.Lock()
# 两种引擎配置：小图不放大、检测侧限制为 max；大图保持 RapidOCR 默认的
# min side 放大策略。两者都关闭 180° 方向分类（工程截图正常朝上），避免每张
# 图额外跑 cls；如需处理倒置截图可设 ENGINEERDIFF_OCR_CLS=1。
_SHARED_ENGINES: dict[str, Any] = {}
_ENGINE_INIT_ERROR: str | None = None
_ENGINE_PROFILES = ("small", "large")


def _package_root() -> Path | None:
    """定位 rapidocr_onnxruntime 安装目录（不 import 重型包）。"""
    try:
        spec = importlib.util.find_spec("rapidocr_onnxruntime")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).resolve().parent


def models_dir() -> Path | None:
    root = _package_root()
    return root / "models" if root else None


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _model_signature() -> str:
    """模型目录指纹（文件名+大小），用于缓存失效判断。"""
    root = models_dir()
    if root is None or not root.is_dir():
        return "missing"
    parts: list[str] = []
    try:
        for path in sorted(root.glob("*.onnx")):
            try:
                parts.append(f"{path.name}:{path.stat().st_size}")
            except OSError:
                parts.append(f"{path.name}:?")
    except OSError:
        return "unreadable"
    return ",".join(parts) or "empty"


def _engine_signature() -> str:
    cls = "on" if os.environ.get("ENGINEERDIFF_OCR_CLS") in ("1", "true", "True") else "off"
    return (
        f"{CACHE_VERSION}|rapidocr={package_version('rapidocr-onnxruntime')}"
        f"|onnxruntime={package_version('onnxruntime')}|models={_model_signature()}"
        f"|profiles=adaptive-v2|cls={cls}|rec_batch=16"
    )


class OcrService:
    """离线 OCR 服务。

    参数 ``cache_dir`` 用于测试隔离；默认使用用户缓存目录。进程内的
    ``RapidOCR`` 引擎是共享单例，多个 ``OcrService`` 实例不会重复加载模型。
    """

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._cache = OcrCache(cache_dir, version=_engine_signature())
        self._infer_lock = _INFER_LOCK

    # ------------------------------------------------------------------ 属性
    @property
    def cache_dir(self) -> Path:
        return self._cache.directory

    def model_info(self) -> dict[str, Any]:
        """返回引擎/模型/缓存信息，不触发模型加载。"""
        root = _package_root()
        mdir = models_dir()
        models: list[dict[str, Any]] = []
        if mdir is not None and mdir.is_dir():
            try:
                for path in sorted(mdir.glob("*.onnx")):
                    entry: dict[str, Any] = {"name": path.name, "path": str(path)}
                    try:
                        entry["size"] = path.stat().st_size
                    except OSError:
                        entry["size"] = 0
                    models.append(entry)
            except OSError:
                pass
        loaded = bool(_SHARED_ENGINES)
        return {
            "engine": "RapidOCR",
            "engine_version": package_version("rapidocr-onnxruntime"),
            "backend": f"ONNX Runtime {package_version('onnxruntime')}",
            "device": "CPU",
            "offline": True,
            "package_dir": str(root) if root else "",
            "model_dir": str(mdir) if mdir else "",
            "models": models,
            "models_present": bool(models),
            "cache_dir": str(self._cache.directory),
            "cache_version": self._cache.version,
            "cache_entries": self._cache.count(),
            "initialized": loaded,
            "loaded_profiles": sorted(_SHARED_ENGINES),
            "init_error": _ENGINE_INIT_ERROR,
        }

    # ------------------------------------------------------------------ 单例
    @classmethod
    def shared(cls, cache_dir: str | Path | None = None) -> "OcrService":
        """返回进程级共享服务。首次调用可指定缓存目录。"""
        global _SHARED_SERVICE
        with _ENGINE_LOCK:
            if _SHARED_SERVICE is None:
                _SHARED_SERVICE = cls(cache_dir)
            return _SHARED_SERVICE

    @staticmethod
    def _profile_for_size(width: int, height: int) -> str:
        """小图（min side < 736）不放大，避免 RapidOCR min-side 策略把 416px 截图
        放大 3 倍；大图保持默认配置以保护小字体。"""
        return "small" if min(width, height) < 736 else "large"

    def _ensure_engine(self, profile: str = "large") -> Any:
        """按需加载共享 RapidOCR 引擎；同一 profile 只创建一次。"""
        global _ENGINE_INIT_ERROR
        if profile in _SHARED_ENGINES:
            return _SHARED_ENGINES[profile]
        with _ENGINE_LOCK:
            if profile in _SHARED_ENGINES:
                return _SHARED_ENGINES[profile]
            mdir = models_dir()
            if mdir is None or not any(mdir.glob("*.onnx")):
                _ENGINE_INIT_ERROR = "未找到随包 OCR 模型文件，无法离线识别"
                raise OcrError(_ENGINE_INIT_ERROR)
            try:
                from rapidocr_onnxruntime import RapidOCR  # 重型 import，延迟到这里
            except Exception as exc:  # pragma: no cover - 环境缺依赖时
                _ENGINE_INIT_ERROR = f"OCR 组件加载失败：{exc}"
                raise OcrError(_ENGINE_INIT_ERROR) from exc
            # 工程截图通常一次出现 13–40 段文字，增大识别 batch 可减少 CPU
            # session 启动/同步开销；16 在 4 核 CPU 的密集文本测试中比默认 6 快约 25%。
            kwargs: dict[str, Any] = {"use_cls": True, "rec_batch_num": 16}
            if os.environ.get("ENGINEERDIFF_OCR_CLS") not in ("1", "true", "True"):
                kwargs["use_cls"] = False
            threads = os.environ.get("ENGINEERDIFF_OCR_THREADS", "").strip()
            if threads.isdigit() and int(threads) > 0:
                kwargs["intra_op_num_threads"] = int(threads)
            if profile == "small":
                # 小图检测不放大；max 1280 只对异常宽/长的图做温和限制。
                kwargs["det_limit_type"] = "max"
                kwargs["det_limit_side_len"] = 1280
            try:
                engine = RapidOCR(**kwargs)
            except Exception as exc:
                _ENGINE_INIT_ERROR = f"OCR 引擎初始化失败（模型文件可能损坏）：{exc}"
                raise OcrError(_ENGINE_INIT_ERROR) from exc
            _SHARED_ENGINES[profile] = engine
            _ENGINE_INIT_ERROR = None
            return engine

    def warmup(self, image_path: str | Path | None = None) -> None:
        """在后台预加载 OCR 模型；传入图片时按尺寸预加载对应 profile。"""
        profile = "large"
        if image_path:
            try:
                width, height = self._image_size(Path(image_path))
                profile = self._profile_for_size(width, height)
            except OcrError:
                pass
        self._ensure_engine(profile)

    # ------------------------------------------------------------------ 公共 API
    def recognize(
        self,
        image_path: str,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> OcrPage:
        """识别单张图片，返回带坐标/置信度的 ``OcrPage``。"""
        path = Path(image_path)
        self._check_cancel(cancel)
        self._report(progress, 5, "读取图片")
        if not path.is_file():
            raise OcrError(f"图片文件不存在：{path}")
        # 只读一次文件：哈希、尺寸、OCR 解码都复用同一份 bytes。
        try:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            with Image.open(io.BytesIO(data)) as image:
                width, height = int(image.width), int(image.height)
        except OSError as exc:
            raise OcrError(f"无法读取图片：{path}（{exc}）") from exc
        profile = self._profile_for_size(width, height)
        cache_key = f"{profile}-{digest}"

        # ---------- 缓存 ----------
        cached = self._cache.load(cache_key)
        if cached is not None:
            page = self._page_from_cache(cached, path)
            if page is not None:
                self._report(progress, 100, "使用 OCR 缓存")
                return page
        self._check_cancel(cancel)

        # ---------- 引擎 ----------
        self._report(progress, 30, "加载 OCR 引擎")
        engine = self._ensure_engine(profile)
        self._check_cancel(cancel)

        # ---------- 推理 ----------
        self._report(progress, 55, "识别文字")
        try:
            # 关键：缓存二次检查 + 推理 + 写缓存都在同一把锁内。
            # 这样拖入图片后的后台预识别与正式比较并发时，后到线程等待后直接
            # 命中缓存，不会对同一张图重复推理。不同图片在 4 核 CPU 上实测
            # 多引擎并发无吞吐收益（ONNX 内部已用满线程），因此保留单推理锁。
            with self._infer_lock:
                cached = self._cache.load(cache_key)
                if cached is not None:
                    page = self._page_from_cache(cached, path)
                    if page is not None:
                        self._report(progress, 100, "使用 OCR 缓存")
                        return page
                started = time.perf_counter()
                result, _elapse = engine(data)
                elapsed = time.perf_counter() - started
                # OCR 调用本身不可中断：返回后才发现取消，丢弃结果且不写缓存
                self._check_cancel(cancel)
                self._report(progress, 90, "整理识别结果")
                boxes, warnings = self._boxes_from_result(result)
                page = OcrPage(
                    path=str(path.resolve()),
                    width=width,
                    height=height,
                    boxes=boxes,
                    cached=False,
                    elapsed=elapsed,
                    warnings=warnings,
                )
                self._cache.store(cache_key, self._page_to_cache(page))
        except OcrCancelled:
            raise
        except OcrError:
            raise
        except Exception as exc:
            raise OcrError(f"OCR 识别失败：{exc}") from exc
        self._report(progress, 100, "OCR 识别完成")
        return page

    def clear_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _image_size(path: Path) -> tuple[int, int]:
        try:
            with Image.open(path) as image:
                return int(image.width), int(image.height)
        except Exception as exc:
            raise OcrError(f"无法读取图片：{path}（{exc}）") from exc

    @staticmethod
    def _report(progress: ProgressCallback | None, percent: int, message: str) -> None:
        if progress is None:
            return
        try:
            progress(int(percent), message)
        except Exception:
            # 进度回调异常不应中断识别
            pass

    @staticmethod
    def _check_cancel(cancel: CancelCallback | None) -> None:
        if cancel is None:
            return
        try:
            cancelled = bool(cancel())
        except Exception:
            cancelled = False
        if cancelled:
            raise OcrCancelled("OCR 识别已取消")

    @staticmethod
    def _boxes_from_result(result: Any) -> tuple[list[OcrBox], list[str]]:
        warnings: list[str] = []
        if not result:
            return [], ["未识别到任何文字"]
        boxes: list[OcrBox] = []
        for item in result:
            try:
                quad_raw = item[0]
                text = str(item[1])
                score = float(item[2]) if len(item) > 2 and item[2] is not None else 0.0
            except (TypeError, ValueError, IndexError):
                continue
            if not text.strip():
                continue
            try:
                quad = [(float(p[0]), float(p[1])) for p in quad_raw]
            except (TypeError, ValueError, IndexError):
                continue
            if len(quad) < 4:
                continue
            bbox = BBox.from_quad(quad)
            if bbox.w <= 0 or bbox.h <= 0:
                continue
            boxes.append(
                OcrBox(
                    id=len(boxes),
                    text=text,
                    confidence=max(0.0, min(1.0, score)),
                    bbox=bbox,
                    quad=quad,
                    raw_text=text,
                )
            )
        if not boxes:
            warnings.append("未识别到任何文字")
        return boxes, warnings

    @staticmethod
    def _page_to_cache(page: OcrPage) -> dict[str, Any]:
        return {
            "path": page.path,
            "width": page.width,
            "height": page.height,
            "engine": page.engine,
            "elapsed": page.elapsed,
            "warnings": list(page.warnings),
            "boxes": [
                {
                    "id": box.id,
                    "text": box.text,
                    "confidence": box.confidence,
                    "bbox": box.bbox.to_list(),
                    "quad": [[float(x), float(y)] for x, y in box.quad],
                }
                for box in page.boxes
            ],
        }

    @staticmethod
    def _page_from_cache(payload: dict[str, Any], path: Path) -> OcrPage | None:
        try:
            boxes = [
                OcrBox(
                    id=int(item.get("id", index)),
                    text=str(item["text"]),
                    confidence=float(item.get("confidence", 0.0)),
                    bbox=BBox(*[float(v) for v in item["bbox"]]),
                    quad=[(float(p[0]), float(p[1])) for p in item.get("quad", [])],
                    raw_text=str(item["text"]),
                )
                for index, item in enumerate(payload["boxes"])
            ]
            page = OcrPage(
                path=str(path.resolve()),
                width=int(payload["width"]),
                height=int(payload["height"]),
                boxes=boxes,
                cached=True,
                elapsed=float(payload.get("elapsed", 0.0)),
                warnings=[str(w) for w in payload.get("warnings", [])],
            )
        except (KeyError, TypeError, ValueError):
            # 损坏的缓存条目直接忽略，回退到重新识别
            return None
        return page


_SHARED_SERVICE: OcrService | None = None


def get_ocr_service(cache_dir: str | Path | None = None) -> OcrService:
    """UI 层使用的进程级 OCR 服务单例。"""
    return OcrService.shared(cache_dir)


__all__ = [
    "OcrError",
    "OcrCancelled",
    "OcrService",
    "get_ocr_service",
    "models_dir",
    "default_cache_dir",
    "ProgressCallback",
    "CancelCallback",
]
