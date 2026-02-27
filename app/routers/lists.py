from fastapi import APIRouter, Depends
from app.models import ListValueRequest
from app.core.auth import require_admin
from app.database import list_list_values, add_list_value, remove_list_value

router = APIRouter(prefix="/api/lists")

@router.get("")
async def get_lists(user=Depends(require_admin)):
    return {
        "blacklist": list_list_values("blacklist"),
        "whitelist": list_list_values("whitelist")
    }

@router.post("/add")
async def add_list(req: ListValueRequest, user=Depends(require_admin)):
    add_list_value(req.list_type, req.value)
    return {"status": True}

@router.post("/remove")
async def remove_list(req: ListValueRequest, user=Depends(require_admin)):
    remove_list_value(req.list_type, req.value)
    return {"status": True}
