from fastapi import APIRouter, HTTPException, status
from schemas import ContactSubmission
from services.s3_service import append_lead_to_csv
import logging

logger = logging.getLogger("pipelyt")
router = APIRouter()

@router.post("/contact")
async def contact_submission(submission: ContactSubmission):
    logger.info(f"Received contact submission from {submission.email}")
    
    # Try to extract dict from pydantic model (support both v1 and v2)
    if hasattr(submission, 'model_dump'):
        submission_data = submission.model_dump()
    else:
        submission_data = submission.dict()
        
    success = append_lead_to_csv(submission_data)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process contact submission. Please try again later."
        )
    
    return {"status": "success", "message": "Thank you for contacting us! We will get back to you soon."}
