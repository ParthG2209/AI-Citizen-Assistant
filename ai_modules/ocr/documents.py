"""Safe loading of uploaded images and PDFs into page images (+ optional PDF text layer).

The file type is decided from magic bytes only; the filename extension and HTTP content type
are never trusted. Pillow and PyMuPDF are imported lazily.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ai_modules.ocr.errors import CorruptDocumentError, FileTooLargeError, UnsupportedFileError
from ai_modules.ocr.paddle_engine import OcrLine

if TYPE_CHECKING:
    from PIL.Image import Image

IMAGE_FORMATS = ("png", "jpeg", "webp")
_EXTENSIONS = {"png": {".png"}, "jpeg": {".jpg", ".jpeg"}, "webp": {".webp"}, "pdf": {".pdf"}}


@dataclass(frozen=True, slots=True)
class InputLimits:
    max_bytes: int = 10 * 1024 * 1024
    max_pages: int = 5
    pdf_dpi: int = 200
    max_pixels: int = 40_000_000  # per page, guards against decompression bombs

    @classmethod
    def from_env(cls) -> InputLimits:
        """Limits from ``ACA_OCR_MAX_FILE_MB``, ``_MAX_PAGES``, ``_PDF_DPI`` and ``_MAX_PIXELS``."""
        return cls(
            max_bytes=int(float(os.getenv("ACA_OCR_MAX_FILE_MB", "10")) * 1024 * 1024),
            max_pages=int(os.getenv("ACA_OCR_MAX_PAGES", "5")),
            pdf_dpi=int(os.getenv("ACA_OCR_PDF_DPI", "200")),
            max_pixels=int(os.getenv("ACA_OCR_MAX_PIXELS", "40000000")),
        )


@dataclass(slots=True)
class LoadedDocument:
    kind: str  # "png" | "jpeg" | "webp" | "pdf"
    pages: list[Image]
    text_layer: list[OcrLine] = field(default_factory=list)  # PDF embedded text, page pixel coords
    warnings: list[str] = field(default_factory=list)


def sniff_format(data: bytes) -> str | None:
    """Detect the real file format from its leading bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    # The PDF spec tolerates junk before the header; Acrobat accepts it within the first 1 KiB.
    if b"%PDF-" in data[:1024]:
        return "pdf"
    return None


def load_document(data: bytes, filename: str, limits: InputLimits) -> LoadedDocument:
    if len(data) > limits.max_bytes:
        raise FileTooLargeError(
            f"File is {len(data) / 1_048_576:.1f} MB; the limit is {limits.max_bytes / 1_048_576:.1f} MB"
        )
    if not data:
        raise CorruptDocumentError("File is empty")
    kind = sniff_format(data)
    if kind is None:
        raise UnsupportedFileError("Unsupported file type; upload a PNG, JPEG or WebP image or a PDF")

    warnings: list[str] = []
    ext = os.path.splitext(filename or "")[1].lower()
    if ext and ext not in _EXTENSIONS[kind]:
        warnings.append(f"File extension '{ext}' does not match its content ({kind}); content was used")

    doc = _load_pdf(data, limits) if kind == "pdf" else _load_image(data, kind, limits)
    doc.warnings[:0] = warnings
    return doc


def _load_image(data: bytes, kind: str, limits: InputLimits) -> LoadedDocument:
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width * height > limits.max_pixels:
                raise FileTooLargeError(
                    f"Image is {width}x{height} pixels; the limit is {limits.max_pixels:,} pixels"
                )
            img.load()
            page = ImageOps.exif_transpose(img).convert("RGB")
    except FileTooLargeError:
        raise
    except Image.DecompressionBombError as exc:
        raise FileTooLargeError(f"Image dimensions are too large: {exc}") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise CorruptDocumentError(f"Image could not be decoded ({kind}): {exc}") from exc
    return LoadedDocument(kind=kind, pages=[page])


def _load_pdf(data: bytes, limits: InputLimits) -> LoadedDocument:
    import pymupdf
    from PIL import Image

    try:
        pdf = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise CorruptDocumentError(f"PDF could not be opened: {exc}") from exc

    with pdf:
        if pdf.needs_pass:
            raise UnsupportedFileError("Password-protected PDFs are not supported")
        if pdf.page_count == 0:
            raise CorruptDocumentError("PDF has no pages")
        if pdf.page_count > limits.max_pages:
            raise FileTooLargeError(f"PDF has {pdf.page_count} pages; the limit is {limits.max_pages}")

        warnings: list[str] = []
        if pdf.is_repaired:
            warnings.append("PDF structure was damaged and has been repaired; check extracted values")
        pages: list[Image.Image] = []
        text_layer: list[OcrLine] = []
        zoom = limits.pdf_dpi / 72
        try:
            for index, page in enumerate(pdf):
                rect = page.rect
                if rect.width * zoom * rect.height * zoom > limits.max_pixels:
                    raise FileTooLargeError(
                        f"PDF page {index + 1} is too large to render at {limits.pdf_dpi} DPI"
                    )
                pix = page.get_pixmap(
                    matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False
                )
                pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
                text_layer.extend(_text_layer_lines(page, index, zoom))
        except FileTooLargeError:
            raise
        except (RuntimeError, ValueError) as exc:
            raise CorruptDocumentError(f"PDF page could not be rendered: {exc}") from exc
    return LoadedDocument(kind="pdf", pages=pages, text_layer=text_layer, warnings=warnings)


def _text_layer_lines(page: object, index: int, zoom: float) -> list[OcrLine]:
    """Embedded text lines (digital PDFs), scaled to the rendered image's pixel coordinates."""
    lines: list[OcrLine] = []
    for block in page.get_text("dict")["blocks"]:  # type: ignore[attr-defined]
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                x0, y0, x1, y1 = (v * zoom for v in line["bbox"])
                lines.append(OcrLine(text, 1.0, (x0, y0, x1, y1), page=index, source="text_layer"))
    return lines
