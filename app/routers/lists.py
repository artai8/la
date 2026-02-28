from fastapi import APIRouter, Depends
from app.models import ListValueRequest
from app.core.database import list_list_values, add_list_value, remove_list_value
from app.core.auth import get_current_user

router = APIRouter(prefix="/api/lists")

@router.get("/{list_type}")
async def get_list(list_type: str, user=Depends(get_current_user)):
    return {"items": list_list_values(list_type)}

@router.post("/add")
async def add_value(req: ListValueRequest, user=Depends(get_current_user)):
    add_list_value(req.list_type, req.value)
    return {"status": True}

@router.post("/remove")
async def remove_value(req: ListValueRequest, user=Depends(get_current_user)):
    remove_list_value(req.list_type, req.value)
    return {"status": True}
