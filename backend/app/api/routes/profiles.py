"""Citizens, profile fields (with provenance) and self-declared scheme enrollments."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.schemas.citizen import (
    CitizenCreate,
    CitizenOut,
    EnrollmentOut,
    EnrollmentUpdate,
    ProfileCatalogOut,
    ProfileOut,
    ProfileUpdate,
)
from app.schemas.common import (
    CITIZEN_NOT_FOUND,
    PAYLOAD_TOO_LARGE,
    SCHEME_NOT_FOUND,
    VALIDATION_ERROR,
    error_responses,
)
from app.services import profile_service

router = APIRouter(tags=["citizens"])

_CITIZEN_READ = error_responses(CITIZEN_NOT_FOUND, VALIDATION_ERROR)


@router.get("/profile-fields", response_model=ProfileCatalogOut, summary="Profile field catalog for the UI")
def get_profile_fields() -> ProfileCatalogOut:
    return profile_service.profile_catalog()


@router.post(
    "/citizens",
    response_model=CitizenOut,
    status_code=201,
    summary="Create a citizen record",
    description="Only synthetic demo records (`is_synthetic: true`) unless the server sets "
    "ALLOW_NON_SYNTHETIC=true.",
    responses=error_responses(
        (403, "non_synthetic_not_allowed: this deployment accepts synthetic demo records only"),
        PAYLOAD_TOO_LARGE,
        VALIDATION_ERROR,
    ),
)
def create_citizen(data: CitizenCreate | None = None, db: Session = Depends(get_db)) -> CitizenOut:
    citizen = profile_service.create_citizen(db, data or CitizenCreate())
    return CitizenOut.model_validate(citizen)


@router.get(
    "/citizens/{citizen_id}",
    response_model=CitizenOut,
    summary="Get a citizen record",
    responses=_CITIZEN_READ,
)
def get_citizen(citizen_id: uuid.UUID, db: Session = Depends(get_db)) -> CitizenOut:
    return CitizenOut.model_validate(profile_service.get_citizen(db, citizen_id))


@router.delete(
    "/citizens/{citizen_id}",
    status_code=204,
    response_class=Response,
    summary="Hard-delete a citizen and all their data",
    description="Deletes the profile, document metadata, evaluations and enrollments (database cascade). "
    "An audit row without personal data is kept.",
    responses=_CITIZEN_READ,
)
def delete_citizen(citizen_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    profile_service.delete_citizen(db, citizen_id)
    return Response(status_code=204)


@router.get(
    "/citizens/{citizen_id}/profile",
    response_model=ProfileOut,
    summary="Get profile with provenance",
    responses=_CITIZEN_READ,
)
def get_profile(citizen_id: uuid.UUID, db: Session = Depends(get_db)) -> ProfileOut:
    return profile_service.get_profile(db, citizen_id)


@router.patch(
    "/citizens/{citizen_id}/profile",
    response_model=ProfileOut,
    summary="Set or clear profile fields",
    description="Changed values become `self_declared` (a correction of an OCR value loses its document "
    "provenance); re-submitting the stored value keeps its provenance; `null` clears a field.",
    responses=error_responses(CITIZEN_NOT_FOUND, PAYLOAD_TOO_LARGE, VALIDATION_ERROR),
)
def update_profile(citizen_id: uuid.UUID, data: ProfileUpdate, db: Session = Depends(get_db)) -> ProfileOut:
    return profile_service.update_profile(db, citizen_id, data.fields)


@router.get(
    "/citizens/{citizen_id}/enrollments",
    response_model=list[EnrollmentOut],
    summary="List scheme enrollments",
    responses=_CITIZEN_READ,
)
def list_enrollments(citizen_id: uuid.UUID, db: Session = Depends(get_db)) -> list[EnrollmentOut]:
    return [EnrollmentOut.model_validate(e) for e in profile_service.list_enrollments(db, citizen_id)]


@router.put(
    "/citizens/{citizen_id}/enrollments/{scheme_id}",
    response_model=EnrollmentOut,
    summary="Record (self-declared) enrollment status for a scheme",
    responses=error_responses(CITIZEN_NOT_FOUND, SCHEME_NOT_FOUND, PAYLOAD_TOO_LARGE, VALIDATION_ERROR),
)
def set_enrollment(
    citizen_id: uuid.UUID, scheme_id: str, data: EnrollmentUpdate, db: Session = Depends(get_db)
) -> EnrollmentOut:
    return EnrollmentOut.model_validate(
        profile_service.set_enrollment(db, citizen_id, scheme_id, data.status)
    )
