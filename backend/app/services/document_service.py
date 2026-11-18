"""Certificate upload: OCR, metadata storage and provenance-aware profile updates.

Behaviour (see also docs/data_provenance.md):
* The file is read with a size cap, its type is sniffed from magic bytes, OCR runs in memory and the
  bytes are discarded. Only metadata and the validated extracted values are stored.
* Re-uploading the same file (same SHA-256) for the same citizen returns the stored document without
  running OCR again (``duplicate: true``, nothing applied).
* Failed uploads (unsupported, corrupt, too large, OCR unavailable) store nothing.
* Extracted values that are valid and do not need review are applied as ``document_extracted``
  (with confidence and ``source_document_id``) following ``utils.provenance.ocr_extraction``:
  absent, matching self-declared and earlier-document values are replaced; a differing
  self-declared or a verified value is kept and reported as a conflict.
* Values that need review or failed validation stay on the document (``review_required``); the user
  confirms them with PATCH /profile, which records them as self_declared. Soft OCR notes keep the
  value (to confirm); hard validation failures drop it.
* When the declared document type contradicts the detected content (OCR ``type_mismatch``), nothing
  is applied: every field goes to ``review_required`` and the response has ``type_mismatch: true``.
* Deleting a document removes profile values still sourced from it, except values the citizen had
  self-declared identically before the upload confirmed them: those revert to ``self_declared``.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
import uuid
from typing import IO, TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai_modules.rule_engine.fields import DOCUMENT_TYPES, FieldValue, Provenance
from app.errors import ApiError, not_found
from app.models.citizen import ProfileField
from app.models.document import Document
from app.schemas.document import (
    AppliedField,
    ConflictField,
    DocumentDeleteOut,
    DocumentOut,
    DocumentUploadOut,
    ReviewField,
)
from app.services import audit_service, profile_service
from app.services.profile_service import ID_NUMBER, InvalidProfileValue, check_profile_value
from app.utils.logger import get_logger
from app.utils.provenance import apply_to_row, field_value_from_row, ocr_extraction

if TYPE_CHECKING:
    from ai_modules.ocr import OcrDocumentResult

logger = get_logger(__name__)

CHUNK_BYTES = 1024 * 1024
MIME_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp", "pdf": "application/pdf"}
_UNSAFE_FILENAME_CHARS = re.compile(r"[^\w.\- ()]+")
# Machine-readable OCR warning prefix: ``declared_type_mismatch:<declared>!=<detected>``.
TYPE_MISMATCH_WARNING = "declared_type_mismatch:"
# Marker stored on an applied extracted item whose value upgraded an identical self-declared value.
_REPLACED_SELF_DECLARED = "replaced_self_declared"


def read_limited(stream: IO[bytes], max_bytes: int) -> bytes:
    """Read an upload in chunks; 413 as soon as it exceeds ``max_bytes``."""
    chunks: list[bytes] = []
    size = 0
    while chunk := stream.read(CHUNK_BYTES):
        size += len(chunk)
        if size > max_bytes:
            raise ApiError(
                413,
                "payload_too_large",
                f"File exceeds the {max_bytes // (1024 * 1024)} MB limit",
                {"max_bytes": max_bytes},
            )
        chunks.append(chunk)
    return b"".join(chunks)


def sanitize_filename(filename: str | None) -> str:
    """Basename only, safe characters, identity-number-like digit runs masked, max 255 chars."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFKC", name)
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = ID_NUMBER.sub("[redacted]", name)
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip(" .")
    return name[-255:] or "upload"


def _validate_declared_type(document_type: str | None) -> None:
    if document_type is None:
        return
    doc = DOCUMENT_TYPES.get(document_type)
    if doc is None:
        raise ApiError(
            422,
            "validation_error",
            "Unknown document type",
            [
                {
                    "loc": ["body", "document_type"],
                    "msg": f"Must be one of {', '.join(DOCUMENT_TYPES)}",
                    "type": "enum",
                }
            ],
        )
    if not doc.ocr_supported:
        supported = [d.id for d in DOCUMENT_TYPES.values() if d.ocr_supported]
        raise ApiError(
            422,
            "document_type_not_ocr_supported",
            f"'{document_type}' cannot be read automatically; enter its values in the profile instead",
            {"ocr_supported_types": supported},
        )


def _run_ocr(data: bytes, filename: str, declared_type: str | None) -> OcrDocumentResult:
    """Call the OCR pipeline (imported lazily: PaddleOCR is heavy) and map its errors to HTTP errors."""
    try:
        from ai_modules import ocr
    except ImportError as exc:  # OCR module dependencies missing entirely
        raise ApiError(503, "ocr_unavailable", "Document OCR is not installed on this server") from exc
    try:
        return ocr.process_document(data, filename, declared_type)
    except ocr.UnsupportedDocumentTypeError as exc:
        raise ApiError(422, "document_type_not_ocr_supported", str(exc)) from exc
    except ocr.UnsupportedFileError as exc:
        raise ApiError(415, "unsupported_media_type", str(exc)) from exc
    except ocr.CorruptDocumentError as exc:
        raise ApiError(422, "corrupt_document", str(exc)) from exc
    except ocr.FileTooLargeError as exc:
        raise ApiError(413, "payload_too_large", str(exc)) from exc
    except ocr.OcrUnavailableError as exc:
        raise ApiError(503, "ocr_unavailable", f"Document OCR is unavailable: {exc}") from exc


def _review_items(extracted: list[dict[str, Any]]) -> list[ReviewField]:
    return [
        ReviewField(
            field=item["name"],
            value=item["value"],
            confidence=item["confidence"],
            validation_errors=item["validation_errors"],
        )
        for item in extracted
        if item["needs_review"] or item["validation_errors"]
    ]


def _extracted_items(result: OcrDocumentResult) -> list[dict[str, Any]]:
    """OCR fields re-validated with the backend's profile rules (no raw_text is kept).

    A field is only eligible for automatic application when it has a valid value, no notes, OCR did
    not flag it, its confidence is at least the OCR review threshold and the declared document type
    does not contradict the content. Soft notes (e.g. OCR vs PDF text layer disagreement) keep the
    value for the citizen to confirm; a hard failure (OCR found no valid value, or the backend's
    profile validation rejects it) drops the value.
    """
    from ai_modules.ocr.validator import review_threshold  # stdlib-only module, no OCR models

    threshold = review_threshold()
    type_mismatch = result.type_mismatch
    items = []
    for f in result.fields:
        value, notes = f.value, list(f.validation_errors)
        if value is None:
            if not notes:
                notes.append("No value could be read")
        else:
            try:
                value = check_profile_value(f.name, value)
            except InvalidProfileValue as exc:
                value, notes = None, [*notes, str(exc)]
        confidence = float(f.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            confidence = 0.0
        items.append(
            {
                "name": f.name,
                "value": value,
                "confidence": round(confidence, 4),
                "needs_review": bool(f.needs_review)
                or bool(notes)
                or value is None
                or type_mismatch
                or confidence < threshold,
                "validation_errors": notes,
            }
        )
    return items


def _has_type_mismatch(warnings: list[str]) -> bool:
    return any(w.startswith(TYPE_MISMATCH_WARNING) for w in warnings)


def _find_duplicate(db: Session, citizen_id: uuid.UUID, sha256: str) -> Document | None:
    return db.scalar(select(Document).where(Document.citizen_id == citizen_id, Document.sha256 == sha256))


def _duplicate_response(document: Document) -> DocumentUploadOut:
    return DocumentUploadOut(
        document=DocumentOut.model_validate(document),
        duplicate=True,
        type_mismatch=_has_type_mismatch(document.warnings),
        applied_fields=[],
        review_required=_review_items(document.extracted_fields),
        conflicts=[],
        missing_fields=list(document.missing_fields),
        warnings=list(document.warnings),
    )


def upload_document(
    db: Session,
    citizen_id: uuid.UUID,
    stream: IO[bytes],
    filename: str | None,
    declared_type: str | None,
    max_bytes: int,
) -> DocumentUploadOut:
    profile_service.get_citizen(db, citizen_id)
    _validate_declared_type(declared_type)
    data = read_limited(stream, max_bytes)
    if not data:
        raise ApiError(422, "corrupt_document", "File is empty")

    from ai_modules.ocr.documents import sniff_format  # stdlib-only helper, no OCR models

    kind = sniff_format(data)
    if kind is None:
        raise ApiError(
            415, "unsupported_media_type", "Unsupported file type; upload a PNG, JPEG or WebP image or a PDF"
        )
    sha256 = hashlib.sha256(data).hexdigest()
    existing = _find_duplicate(db, citizen_id, sha256)
    if existing is not None:
        return _duplicate_response(existing)
    safe_name = sanitize_filename(filename)
    db.rollback()  # end the read transaction; OCR can take seconds

    result = _run_ocr(data, safe_name, declared_type)
    size = len(data)
    del data  # the file content is never stored

    extracted = _extracted_items(result)
    confidences = [item["confidence"] for item in extracted]
    document = Document(
        id=uuid.uuid4(),
        citizen_id=citizen_id,
        document_type=result.document_type,
        original_filename=safe_name,
        content_type=MIME_TYPES[kind],
        size_bytes=size,
        sha256=sha256,
        status="processed",
        ocr_engine=result.engine[:128],
        mean_confidence=round(sum(confidences) / len(confidences), 4) if confidences else None,
        classification_confidence=round(float(result.classification_confidence), 4),
        page_count=result.page_count,
        extracted_fields=extracted,
        missing_fields=list(result.missing_fields),
        warnings=list(result.warnings),
    )
    citizen = profile_service.get_citizen(db, citizen_id)  # may have been deleted during OCR -> 404
    db.add(document)
    try:
        db.flush()
    except IntegrityError:  # the same file was uploaded concurrently
        db.rollback()
        duplicate = _find_duplicate(db, citizen_id, sha256)
        if duplicate is None:
            raise
        return _duplicate_response(duplicate)

    rows = {
        r.field_name: r for r in db.scalars(select(ProfileField).where(ProfileField.citizen_id == citizen_id))
    }
    applied: list[AppliedField] = []
    conflicts: list[ConflictField] = []
    replaced_self_declared: set[str] = set()
    for item in extracted:
        if item["needs_review"] or item["validation_errors"]:
            continue
        name, value, confidence = item["name"], item["value"], item["confidence"]
        row = rows.get(name)
        current = field_value_from_row(row) if row is not None else None
        new = ocr_extraction(current, value, confidence, document.id)
        if new is None:
            if current is not None and current.value != value:
                conflicts.append(
                    ConflictField(
                        field=name,
                        extracted_value=value,
                        confidence=confidence,
                        current_value=current.value,
                        current_provenance=current.provenance,
                    )
                )
            continue
        if row is None:
            row = ProfileField(citizen_id=citizen_id, field_name=name)
            db.add(row)
        apply_to_row(row, new)
        if current is not None and current.provenance is Provenance.SELF_DECLARED:
            replaced_self_declared.add(name)
        applied.append(
            AppliedField(
                field=name,
                value=value,
                confidence=confidence,
                previous_provenance=current.provenance if current else None,
            )
        )
    if replaced_self_declared:  # new list: in-place JSONB mutation would not be persisted
        document.extracted_fields = [
            {**item, _REPLACED_SELF_DECLARED: True} if item["name"] in replaced_self_declared else item
            for item in extracted
        ]

    if applied:
        citizen.updated_at = func.now()
    audit_service.record(
        db,
        "document",
        document.id,
        "upload",
        {
            "citizen_id": str(citizen_id),
            "document_type": document.document_type,
            "type_mismatch": result.type_mismatch,
            "applied_fields": [a.field for a in applied],
            "conflict_fields": [c.field for c in conflicts],
        },
    )
    db.commit()
    db.refresh(document)
    review = _review_items(extracted)
    logger.info(
        "document_processed",
        extra={
            "citizen_id": str(citizen_id),
            "document_id": str(document.id),
            "document_type": document.document_type,
            "applied_fields": [a.field for a in applied],
            "review_fields": [r.field for r in review],
            "conflict_fields": [c.field for c in conflicts],
        },
    )
    return DocumentUploadOut(
        document=DocumentOut.model_validate(document),
        duplicate=False,
        type_mismatch=result.type_mismatch or _has_type_mismatch(document.warnings),
        applied_fields=applied,
        review_required=review,
        conflicts=conflicts,
        missing_fields=document.missing_fields,
        warnings=document.warnings,
    )


def list_documents(db: Session, citizen_id: uuid.UUID) -> list[DocumentOut]:
    profile_service.get_citizen(db, citizen_id)
    documents = db.scalars(
        select(Document).where(Document.citizen_id == citizen_id).order_by(Document.created_at.desc())
    )
    return [DocumentOut.model_validate(d) for d in documents]


def get_document(db: Session, document_id: uuid.UUID) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise not_found("document", document_id)
    return document


def delete_document(db: Session, document_id: uuid.UUID) -> DocumentDeleteOut:
    """Delete the metadata and the profile values still sourced from this document.

    A value the citizen had self-declared identically before this document confirmed it reverts to
    ``self_declared`` (the declaration predates the document); every other sourced value is removed.
    """
    document = get_document(db, document_id)
    sourced = list(
        db.scalars(
            select(ProfileField).where(
                ProfileField.source_document_id == document_id,
                ProfileField.provenance == Provenance.DOCUMENT_EXTRACTED.value,
            )
        )
    )
    declared_before = {
        item["name"] for item in document.extracted_fields if item.get(_REPLACED_SELF_DECLARED)
    }
    removed: list[str] = []
    reverted: list[str] = []
    for row in sourced:
        if row.field_name in declared_before:
            apply_to_row(row, FieldValue(value=row.value, provenance=Provenance.SELF_DECLARED))
            reverted.append(row.field_name)
        else:
            db.delete(row)
            removed.append(row.field_name)
    removed.sort()
    reverted.sort()
    if removed or reverted:
        profile_service.get_citizen(db, document.citizen_id).updated_at = func.now()
    audit_service.record(
        db,
        "document",
        document_id,
        "delete",
        {
            "citizen_id": str(document.citizen_id),
            "removed_profile_fields": removed,
            "reverted_profile_fields": reverted,
        },
    )
    db.flush()  # clear source_document_id on reverted rows before the document row goes
    db.delete(document)
    db.commit()
    logger.info(
        "document_deleted",
        extra={"document_id": str(document_id), "removed_fields": removed, "reverted_fields": reverted},
    )
    return DocumentDeleteOut(
        document_id=document_id, removed_profile_fields=removed, reverted_profile_fields=reverted
    )
