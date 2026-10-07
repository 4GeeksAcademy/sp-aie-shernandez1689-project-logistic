from app.db import applications_table
from app.models import ApplicationSubmissionRecord


def create_application_submission(payload: dict) -> ApplicationSubmissionRecord:
    submission = ApplicationSubmissionRecord(**payload)
    applications_table.insert(submission.model_dump())
    return submission