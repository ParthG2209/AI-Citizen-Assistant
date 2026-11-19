"""Lazy, cached PaddleOCR (PP-OCR) inference wrapper.

Text is detected once per page with a PP-OCR detection model, then every detected line is
recognised by one recognition model per configured language; per line, the reading with
the highest confidence wins. This lets mixed Hindi/English certificates be read with the
English (Latin) and Devanagari recognisers without running two full pipelines.

Configuration (environment variables):

* ``ACA_OCR_LANGS``       comma-separated recognisers, subset of ``en,hi`` (default ``en,hi``)
* ``ACA_OCR_DET_MODEL``   PaddleOCR detection model (default ``PP-OCRv5_mobile_det``)
* ``ACA_OCR_DEVICE``      ``cpu`` (default) or e.g. ``gpu:0`` when a CUDA build of Paddle is installed
* ``ACA_OCR_MKLDNN``      oneDNN CPU acceleration for the recognisers (default ``1``; ~3x faster)
* ``ACA_OCR_DET_MKLDNN``  oneDNN for the detector (default ``0``: with paddlepaddle 3.3 the
  PP-OCRv5 detector fails under oneDNN with "ConvertPirAttribute2RuntimeAttribute not support")
* ``ACA_OCR_CPU_THREADS`` CPU inference threads (default: PaddleX default)
* ``PADDLE_PDX_CACHE_HOME`` where PaddleX stores downloaded models (default ``~/.paddlex``;
  models land in ``<cache>/official_models``)

Pre-download (and smoke-test) the models (≈ 20 MB) with
``python -m ai_modules.ocr.paddle_engine --download``.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai_modules.ocr.errors import OcrUnavailableError

if TYPE_CHECKING:
    from PIL.Image import Image

# Recognition model per supported language code. Both models also read ASCII digits.
REC_MODELS: dict[str, str] = {
    "en": "en_PP-OCRv5_mobile_rec",
    "hi": "devanagari_PP-OCRv5_mobile_rec",
}
DEFAULT_DET_MODEL = "PP-OCRv5_mobile_det"
DEVANAGARI_MIN_SCORE = 0.6


@dataclass(frozen=True, slots=True)
class OcrLine:
    """One recognised text line. ``bbox`` is (x0, y0, x1, y1) in page pixels."""

    text: str
    confidence: float
    bbox: tuple[float, float, float, float]
    page: int = 0
    source: str = "ocr"  # "ocr" | "text_layer"
    lang: str | None = None


def _env_flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True, slots=True)
class EngineConfig:
    langs: tuple[str, ...] = ("en", "hi")
    det_model: str = DEFAULT_DET_MODEL
    device: str = "cpu"
    rec_mkldnn: bool = True
    det_mkldnn: bool = False
    cpu_threads: int | None = None

    @classmethod
    def from_env(cls) -> EngineConfig:
        langs = tuple(
            code.strip().lower() for code in os.getenv("ACA_OCR_LANGS", "en,hi").split(",") if code.strip()
        )
        unknown = [code for code in langs if code not in REC_MODELS]
        if unknown or not langs:
            raise OcrUnavailableError(
                f"ACA_OCR_LANGS must be a subset of {', '.join(REC_MODELS)}; got {','.join(langs)!r}"
            )
        threads = os.getenv("ACA_OCR_CPU_THREADS")
        return cls(
            langs=langs,
            det_model=os.getenv("ACA_OCR_DET_MODEL", DEFAULT_DET_MODEL),
            device=os.getenv("ACA_OCR_DEVICE", "cpu"),
            rec_mkldnn=_env_flag("ACA_OCR_MKLDNN", "1"),
            det_mkldnn=_env_flag("ACA_OCR_DET_MKLDNN", "0"),
            cpu_threads=int(threads) if threads else None,
        )

    def predictor_kwargs(self, mkldnn: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"device": self.device, "enable_mkldnn": mkldnn}
        if self.cpu_threads:
            kwargs["cpu_threads"] = self.cpu_threads
        return kwargs


def ocr_available() -> bool:
    """True when the PaddleOCR packages are importable (models may still need downloading)."""
    return (
        importlib.util.find_spec("paddleocr") is not None and importlib.util.find_spec("paddle") is not None
    )


def model_cache_dir() -> Path:
    """Directory where PaddleX keeps downloaded official models."""
    base = os.getenv("PADDLE_PDX_CACHE_HOME") or str(Path.home() / ".paddlex")
    return Path(base) / "official_models"


class PaddleEngine:
    """Detection + multi-language recognition. Thread-safe (inference is serialised)."""

    def __init__(self, config: EngineConfig) -> None:
        if not ocr_available():
            raise OcrUnavailableError(
                "PaddleOCR is not installed; install ai_modules/requirements.txt (paddlepaddle, paddleocr)"
            )
        # Skip PaddleX's slow connectivity check of every model hoster on import.
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        try:
            from paddleocr import TextDetection, TextRecognition

            self._det = TextDetection(
                model_name=config.det_model, **config.predictor_kwargs(config.det_mkldnn)
            )
            rec_kwargs = config.predictor_kwargs(config.rec_mkldnn)
            self._recs = {
                lang: TextRecognition(model_name=REC_MODELS[lang], **rec_kwargs) for lang in config.langs
            }
            import paddleocr

            version = paddleocr.__version__
        except OcrUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - any load failure means OCR is unavailable
            raise OcrUnavailableError(
                f"PaddleOCR models could not be loaded ({type(exc).__name__}: {exc}). "
                f"Models are cached in {model_cache_dir()}; pre-download them with "
                "`python -m ai_modules.ocr.paddle_engine --download` while online."
            ) from exc
        self.config = config
        rec_names = ", ".join(REC_MODELS[lang] for lang in config.langs)
        self.name = f"paddleocr-{version} ({config.det_model} + {rec_names})"
        self._lock = threading.Lock()

    def recognize(self, image: Image, page: int = 0) -> list[OcrLine]:
        """OCR one RGB page image; lines are returned in reading order (top-down, left-right)."""
        import numpy as np

        bgr = np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])
        with self._lock:
            try:
                det = next(iter(self._det.predict(bgr, batch_size=1)))
                polys = [np.asarray(p, dtype=np.float32) for p in det["dt_polys"]]
                if not polys:
                    return []
                crops = [_crop_line(image, poly) for poly in polys]
                crop_arrays = [np.ascontiguousarray(np.asarray(c)[:, :, ::-1]) for c in crops]
                readings: list[list[tuple[str, float, str]]] = [[] for _ in polys]
                for lang, rec in self._recs.items():
                    # batch_size=1: batching pads every crop to the widest line, which is slower on CPU.
                    for idx, res in enumerate(rec.predict(crop_arrays, batch_size=1)):
                        readings[idx].append((str(res["rec_text"]), float(res["rec_score"]), lang))
            except Exception as exc:  # noqa: BLE001 - runtime/inference failure of the Paddle backend
                raise OcrUnavailableError(
                    f"PaddleOCR inference failed ({type(exc).__name__}: {exc}); "
                    "if this mentions oneDNN/PIR, set ACA_OCR_MKLDNN=0 / ACA_OCR_DET_MKLDNN=0"
                ) from exc

        lines: list[OcrLine] = []
        for poly, options in zip(polys, readings, strict=True):
            text, score, lang = choose_reading(options)
            text = text.strip()
            if not text:
                continue
            xs, ys = poly[:, 0], poly[:, 1]
            bbox = (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))
            lines.append(OcrLine(text, round(score, 4), bbox, page=page, source="ocr", lang=lang))
        return sort_reading_order(lines)


def choose_reading(options: list[tuple[str, float, str]]) -> tuple[str, float, str]:
    """Pick one (text, score, lang) reading of a line from the per-language recognisers.

    The Latin model confidently misreads Devanagari as Latin gibberish (e.g. "जिला" -> "fuell"),
    while the Devanagari model also reads Latin letters and digits. So a Devanagari reading that
    actually contains Devanagari script wins if reasonably confident; otherwise the highest score.
    """
    for text, score, lang in options:
        if lang == "hi" and score >= DEVANAGARI_MIN_SCORE and any("\u0900" <= ch <= "\u097f" for ch in text):
            return text, score, lang
    return max(options, key=lambda r: r[1])


def _crop_line(image: Image, poly: Any) -> Image:
    """Rectify a (possibly rotated) quadrilateral text region into an upright crop."""
    import numpy as np
    from PIL import Image as PILImage

    tl, tr, br, bl = poly[:4]
    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl), 1))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr), 1))
    quad = (*tl, *bl, *br, *tr)  # PIL QUAD order: upper-left, lower-left, lower-right, upper-right
    crop = image.transform(
        (width, height), PILImage.Transform.QUAD, data=quad, resample=PILImage.Resampling.BICUBIC
    )
    if height > width * 1.5:  # vertical text box: rotate so the recogniser sees a horizontal line
        crop = crop.rotate(90, expand=True)
    return crop


def sort_reading_order(lines: list[OcrLine]) -> list[OcrLine]:
    """Sort by page, then rows (vertical centre within half a line height), then x."""
    ordered = sorted(lines, key=lambda ln: (ln.page, (ln.bbox[1] + ln.bbox[3]) / 2, ln.bbox[0]))
    rows: list[list[OcrLine]] = []
    for line in ordered:
        centre = (line.bbox[1] + line.bbox[3]) / 2
        if rows and rows[-1][0].page == line.page:
            ref = rows[-1][0]
            ref_centre = (ref.bbox[1] + ref.bbox[3]) / 2
            if abs(centre - ref_centre) <= max(ref.bbox[3] - ref.bbox[1], 1) / 2:
                rows[-1].append(line)
                continue
        rows.append([line])
    return [line for row in rows for line in sorted(row, key=lambda ln: ln.bbox[0])]


@lru_cache(maxsize=4)
def _engine_for(config: EngineConfig) -> PaddleEngine:
    return PaddleEngine(config)


def get_engine(config: EngineConfig | None = None) -> PaddleEngine:
    """Return the process-wide engine for ``config`` (default: from environment), loading it once."""
    return _engine_for(config or EngineConfig.from_env())


def download_models(config: EngineConfig | None = None) -> Path:
    """Load (and therefore download) every configured model and run one tiny inference.

    Returns the model cache directory. Raises ``OcrUnavailableError`` if the runtime cannot infer.
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (320, 64), "white")
    ImageDraw.Draw(image).text((10, 20), "INCOME 2025", fill="black")
    get_engine(config).recognize(image)
    return model_cache_dir()


def _main() -> None:
    parser = argparse.ArgumentParser(description="PaddleOCR engine utilities")
    parser.add_argument("--download", action="store_true", help="download the configured models and exit")
    parser.add_argument("image", nargs="?", help="optional image file to OCR and print line by line")
    args = parser.parse_args()
    if args.download:
        path = download_models()
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        print(f"Models ready in {path} ({size / 1_048_576:.1f} MB)")
    if args.image:
        from PIL import Image

        engine = get_engine()
        start = time.perf_counter()
        with Image.open(args.image) as img:
            lines = engine.recognize(img.convert("RGB"))
        for line in lines:
            print(f"{line.confidence:.3f} [{line.lang}] {line.text}")
        print(f"{len(lines)} lines in {time.perf_counter() - start:.2f}s using {engine.name}")
    if not (args.download or args.image):
        parser.print_help()


if __name__ == "__main__":
    _main()
