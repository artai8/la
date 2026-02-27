from fastapi import APIRouter, Depends
from app.models import UserCreateRequest, TaskIdRequest
from app.core.auth import require_admin
from app.database import list_users, create_user, remove_user

router = APIRouter(prefix="/api/users")

@router.get("")
async def users_list(user=Depends(require_admin)):
    return {"items": list_users()}

@router.post("/add")
async def users_add(req: UserCreateRequest, user=Depends(require_admin)):
    create_user(req.username.strip(), req.password, req.role)
    return {"status": True}

@router.post("/remove")
async def users_remove(req: TaskIdRequest, user=Depends(require_admin)):
    remove_user(req.id)
    return {"status": True}
