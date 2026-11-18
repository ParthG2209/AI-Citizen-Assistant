"""Document intelligence: PaddleOCR-based extraction of profile fields from MP certificates.

Importing this package is cheap: PaddleOCR, Pillow and PyMuPDF are only imported when a
document is processed.
"""

from ai_modules.ocr.errors import (
    CorruptDocumentError,
    FileTooLargeError,
    OcrError,
    OcrUnavailableError,
    UnsupportedDocumentTypeError,
    UnsupportedFileError,
)
from ai_modules.ocr.paddle_engine import OcrLine, ocr_available
from ai_modules.ocr.pipeline import OcrDocumentResult, process_document
from ai_modules.ocr.validator import ExtractedField

__all__ = [
    "CorruptDocumentError",
    "ExtractedField",
    "FileTooLargeError",
    "OcrDocumentResult",
    "OcrError",
    "OcrLine",
    "OcrUnavailableError",
    "UnsupportedDocumentTypeError",
    "UnsupportedFileError",
    "ocr_available",
    "process_document",
]
