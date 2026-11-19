"""Upload validation happens before any model is loaded, so these tests need no OCR models."""

import io

import pytest

from ai_modules.ocr import (
    CorruptDocumentError,
    FileTooLargeError,
    UnsupportedDocumentTypeError,
    UnsupportedFileError,
    process_document,
)
from ai_modules.ocr.documents import InputLimits, load_document, sniff_format

PIL = pytest.importorskip("PIL")
pymupdf = pytest.importorskip("pymupdf")


def png_bytes(size=(64, 32), color="white"):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def pdf_bytes(pages=1):
    with pymupdf.open() as pdf:
        for _ in range(pages):
            pdf.new_page().insert_text((72, 72), "hello")
        return pdf.tobytes()


def test_sniff_format_uses_magic_bytes():
    assert sniff_format(png_bytes()) == "png"
    assert sniff_format(b"\xff\xd8\xff\xe0rest") == "jpeg"
    assert sniff_format(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert sniff_format(b"junk\n%PDF-1.7\n") == "pdf"
    assert sniff_format(b"GIF89a....") is None
    assert sniff_format(b"hello world") is None


def test_unsupported_file_rejected_regardless_of_extension():
    with pytest.raises(UnsupportedFileError):
        process_document(b"GIF89a\x01\x00\x01\x00", "certificate.png")
    with pytest.raises(UnsupportedFileError):
        process_document(b"This is plain text pretending to be a PDF", "certificate.pdf")


def test_extension_mismatch_is_a_warning_not_an_error():
    doc = load_document(png_bytes(), "scan.pdf", InputLimits())
    assert doc.kind == "png" and len(doc.pages) == 1
    assert any("does not match" in w for w in doc.warnings)


def test_unknown_or_non_ocr_declared_type_rejected():
    with pytest.raises(UnsupportedDocumentTypeError):
        process_document(png_bytes(), "a.png", declared_type="land_record")
    with pytest.raises(UnsupportedDocumentTypeError):
        process_document(png_bytes(), "a.png", declared_type="passport")


def test_empty_and_corrupt_files():
    with pytest.raises(CorruptDocumentError):
        process_document(b"", "empty.png")
    with pytest.raises(CorruptDocumentError):
        process_document(png_bytes()[:40], "truncated.png")
    with pytest.raises(CorruptDocumentError):
        process_document(b"\xff\xd8\xff\xe0" + b"\x00" * 200, "broken.jpg")
    with pytest.raises(CorruptDocumentError):
        process_document(b"%PDF-1.7\n" + bytes(range(256)) * 20, "broken.pdf")


def test_oversized_file_rejected(monkeypatch):
    big = png_bytes() + b"\x00" * (10 * 1024 * 1024)
    with pytest.raises(FileTooLargeError):
        process_document(big, "big.png")
    monkeypatch.setenv("ACA_OCR_MAX_FILE_MB", "0.0001")
    with pytest.raises(FileTooLargeError):
        process_document(png_bytes(), "small.png")


def test_pixel_and_page_limits():
    with pytest.raises(FileTooLargeError):
        load_document(png_bytes(size=(2000, 2000)), "a.png", InputLimits(max_pixels=1_000_000))
    with pytest.raises(FileTooLargeError):
        load_document(pdf_bytes(pages=3), "a.pdf", InputLimits(max_pages=2))


def test_pdf_pages_rendered_with_text_layer():
    doc = load_document(pdf_bytes(pages=2), "a.pdf", InputLimits(pdf_dpi=100))
    assert doc.kind == "pdf" and len(doc.pages) == 2
    width, height = doc.pages[0].size
    assert abs(width - 595 * 100 / 72) <= 1 and abs(height - 842 * 100 / 72) <= 1
    assert [line.text for line in doc.text_layer] == ["hello", "hello"]
    assert {line.page for line in doc.text_layer} == {0, 1}


def test_encrypted_pdf_rejected():
    with pymupdf.open() as pdf:
        pdf.new_page()
        data = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    with pytest.raises(UnsupportedFileError):
        load_document(data, "locked.pdf", InputLimits())
