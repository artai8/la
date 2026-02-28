import secrets
from fastapi import APIRouter, Depends, HTTPException
from app.models import UserCreateRequest
from app.core.database import list_users, create_user, remove_user, get_user_by_username
from app.core.auth import get_current_user, hash_password

router = APIRouter(prefix="/api/users")

@router.get("")
async def get_users_list(user=Depends(get_current_user)):
    return {"users": list_users()}

@router.post("")
async def create_new_user(req: UserCreateRequest, user=Depends(get_current_user)):
    if get_user_by_username(req.username):
        return {"status": False, "message": "User exists"}
    
    salt = secrets.token_hex(8)
    phash = hash_password(req.password, salt)
    create_user(req.username, phash, salt, req.role)
    return {"status": True}

@router.post("/{user_id}/remove")
async def delete_user(user_id: int, user=Depends(get_current_user)):
    remove_user(user_id)
    return {"status": True}
