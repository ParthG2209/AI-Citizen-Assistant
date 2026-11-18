"""Certificate upload with OCR, document metadata and deletion."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dependencies import get_db
from app.schemas.common import ERROR_RESPONSES, ErrorResponse
from app.schemas.document import DocumentDeleteOut, DocumentOut, DocumentUploadOut
from app.services import document_service

RESPONSES: dict[int | str, dict[str, Any]] = {
    **ERROR_RESPONSES,
    413: {"model": ErrorResponse, "description": "payload_too_large"},
    415: {"model": ErrorResponse, "description": "unsupported_media_type (decided from the file's bytes)"},
    503: {"model": ErrorResponse, "description": "ocr_unavailable"},
}
router = APIRouter(tags=["documents"], responses=RESPONSES)


@router.post(
    "/citizens/{citizen_id}/documents",
    response_model=DocumentUploadOut,
    status_code=201,
    summary="Upload a certificate (PNG/JPEG/WebP/PDF) and apply OCR-extracted values",
    description="The file is processed in memory and not stored. Valid, confident values become "
    "`document_extracted`; low-confidence or invalid ones are returned in `review_required`; values that "
    "differ from what the citizen entered are returned in `conflicts` and not applied. Uploading the same "
    "file again returns the existing document with status 200 and `duplicate: true` (no OCR re-run).",
    responses={200: {"model": DocumentUploadOut, "description": "Duplicate upload: existing document"}},
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


@router.get("/citizens/{citizen_id}/documents", response_model=list[DocumentOut], summary="List documents")
def list_documents(citizen_id: uuid.UUID, db: Session = Depends(get_db)) -> list[DocumentOut]:
    return document_service.list_documents(db, citizen_id)


@router.get("/documents/{document_id}", response_model=DocumentOut, summary="Document metadata")
def get_document(document_id: uuid.UUID, db: Session = Depends(get_db)) -> DocumentOut:
    return DocumentOut.model_validate(document_service.get_document(db, document_id))


@router.delete(
    "/documents/{document_id}",
    response_model=DocumentDeleteOut,
    summary="Delete a document and the profile values still sourced from it",
)
def delete_document(document_id: uuid.UUID, db: Session = Depends(get_db)) -> DocumentDeleteOut:
    return document_service.delete_document(db, document_id)
