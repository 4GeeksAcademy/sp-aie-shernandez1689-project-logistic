from fastapi import APIRouter, status

from app.models import ApplicationSubmissionRequest, ApplicationSubmissionResponse
from app.services.applications import create_application_submission


router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("", response_model=ApplicationSubmissionResponse, status_code=status.HTTP_201_CREATED)
def submit_application(payload: ApplicationSubmissionRequest) -> ApplicationSubmissionResponse:
    submission = create_application_submission(payload.model_dump(mode="json"))
    return ApplicationSubmissionResponse(
        id=submission.id,
        status="received",
        message="Application submitted successfully",
    )