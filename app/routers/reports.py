import csv
import io
from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.models import WorkerPingRequest
from app.core.auth import get_current_user, require_admin
from app.database import list_reports, list_workers, upsert_worker

router = APIRouter(prefix="/api")

@router.get("/reports/summary")
async def report_summary(start: Optional[int] = None, end: Optional[int] = None, user=Depends(get_current_user)):
    return list_reports(start, end)

@router.get("/reports/export")
async def report_export(start: Optional[int] = None, end: Optional[int] = None, user=Depends(get_current_user)):
    data = list_reports(start, end)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["metric", "value"])
    for k, v in data.items():
        writer.writerow([k, v])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv")

@router.get("/workers")
async def workers_list(user=Depends(require_admin)):
    return {"items": list_workers()}

@router.post("/workers/ping")
async def workers_ping(req: WorkerPingRequest):
    upsert_worker(req.name, req.status)
    return {"status": True}
