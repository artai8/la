from fastapi import APIRouter, Depends
from app.core.database import list_reports
from app.core.auth import get_current_user
import time

router = APIRouter(prefix="/api/reports")

@router.get("")
async def reports_index(start: int = None, end: int = None, user=Depends(get_current_user)):
    # Default to last 24h if not specified? Or all time?
    # Context suggests simple counts.
    return {"data": list_reports(start, end)}
