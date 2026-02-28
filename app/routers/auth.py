import secrets
from fastapi import APIRouter, Response, Depends, HTTPException
from app.models import LoginRequest, BootstrapRequest
from app.core.database import get_user_by_username, create_user, create_session, revoke_session, list_users
from app.core.auth import get_current_user, hash_password

router = APIRouter(prefix="/api/auth")

@router.post("/login")
async def login(req: LoginRequest, response: Response):
    user = get_user_by_username(req.username)
    if not user:
        raise HTTPException(status_code=400, detail="User not found")
    
    phash = hash_password(req.password, user["salt"])
    if phash != user["password_hash"]:
        raise HTTPException(status_code=400, detail="Invalid password")
    
    token = create_session(user["id"])
    response.set_cookie(key="access_token", value=token, httponly=True)
    return {"status": True, "token": token}

@router.post("/logout")
async def logout(request: Response, user=Depends(get_current_user)):
    request.delete_cookie("access_token")
    if user:
        revoke_session(user["token"])
    return {"status": True}

@router.get("/me")
async def get_me(user=Depends(get_current_user)):
    return {"status": True, "user_id": user["user_id"]}

@router.post("/bootstrap")
async def bootstrap(req: BootstrapRequest):
    users = list_users()
    if users:
        raise HTTPException(status_code=400, detail="Already bootstrapped")
    
    salt = secrets.token_hex(8)
    phash = hash_password(req.password, salt)
    create_user(req.username, phash, salt, "admin")
    return {"status": True}
