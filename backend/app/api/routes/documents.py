"""Certificate upload with OCR, document metadata and deletion."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dependencies import get_db
from app.schemas.common import CITIZEN_NOT_FOUND, PAYLOAD_TOO_LARGE, VALIDATION_ERROR, error_responses
from app.schemas.document import DocumentDeleteOut, DocumentOut, DocumentUploadOut
from app.services import document_service

router = APIRouter(tags=["documents"])

_DOCUMENT_NOT_FOUND = error_responses((404, "document_not_found"), VALIDATION_ERROR)


@router.post(
    "/citizens/{citizen_id}/documents",
    response_model=DocumentUploadOut,
    status_code=201,
    summary="Upload a certificate (PNG/JPEG/WebP/PDF) and apply OCR-extracted values",
    description="The file is processed in memory and not stored; its type is sniffed from the bytes (the "
    "client Content-Type is ignored). Valid values with confidence >= the OCR review threshold (0.80) and "
    "no OCR notes become `document_extracted`; others are returned in `review_required` (with their value "
    "when only a soft note applies, null when validation failed); values that differ from what the citizen "
    "entered are returned in `conflicts` and not applied. When the declared `document_type` contradicts "
    "the detected content, nothing is applied and `type_mismatch` is true. Uploading the same file again "
    "returns the existing document with status 200 and `duplicate: true` (no OCR re-run, nothing applied).",
    responses={
        200: {"model": DocumentUploadOut, "description": "Duplicate upload: existing document"},
        **error_responses(
            CITIZEN_NOT_FOUND,
            PAYLOAD_TOO_LARGE,
            (415, "unsupported_media_type: decided from the file's bytes"),
            VALIDATION_ERROR,
            (422, "document_type_not_ocr_supported"),
            (422, "corrupt_document"),
            (503, "ocr_unavailable"),
        ),
    },
)
def upload_document(
    citizen_id: uuid.UUID,
    response: Response,
    file: UploadFile = File(description="Certificate image or PDF"),
    document_type: str | None = Form(default=None, description="Declared type (see GET /profile-fields)"),
    db: Session = Depends(get_db),
) -> DocumentUploadOut:
    # Sync endpoint: FastAPI runs it (and the CPU-bound OCR inside) in its threadpool.
    result = document_service.upload_document(
        db, citizen_id, file.file, file.filename, document_type or None, get_settings().max_upload_bytes
    )
    if result.duplicate:
        response.status_code = 200
    return result


@router.get(
    "/citizens/{citizen_id}/documents",
    response_model=list[DocumentOut],
    summary="List documents",
    responses=error_responses(CITIZEN_NOT_FOUND, VALIDATION_ERROR),
)
def list_documents(citizen_id: uuid.UUID, db: Session = Depends(get_db)) -> list[DocumentOut]:
    return document_service.list_documents(db, citizen_id)


@router.get(
    "/documents/{document_id}",
    response_model=DocumentOut,
    summary="Document metadata",
    responses=_DOCUMENT_NOT_FOUND,
)
def get_document(document_id: uuid.UUID, db: Session = Depends(get_db)) -> DocumentOut:
    return DocumentOut.model_validate(document_service.get_document(db, document_id))


@router.delete(
    "/documents/{document_id}",
    response_model=DocumentDeleteOut,
    summary="Delete a document and the profile values still sourced from it",
    description="Values still sourced from the document are removed (`removed_profile_fields`), except "
    "values the citizen had self-declared identically before the upload confirmed them: those revert to "
    "`self_declared` (`reverted_profile_fields`). Values the citizen corrected are untouched.",
    responses=_DOCUMENT_NOT_FOUND,
)
def delete_document(document_id: uuid.UUID, db: Session = Depends(get_db)) -> DocumentDeleteOut:
    return document_service.delete_document(db, document_id)
