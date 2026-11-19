"""Errors raised by the OCR pipeline. The backend maps them to HTTP 4xx/503 responses."""

from __future__ import annotations


class OcrError(Exception):
    """Base class for all document-processing errors."""


class UnsupportedFileError(OcrError):
    """The upload is not a supported image/PDF (decided by magic bytes, not by extension)."""


class UnsupportedDocumentTypeError(UnsupportedFileError):
    """``declared_type`` is unknown or not an OCR-supported document type."""


class CorruptDocumentError(OcrError):
    """The file claims a supported format but cannot be decoded (truncated, damaged, empty)."""


class FileTooLargeError(OcrError):
    """The file exceeds the configured byte, page-count or pixel limits."""


class OcrUnavailableError(OcrError):
    """PaddleOCR (or its models) cannot be loaded on this machine."""
