from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from app.models import LoginRequest, BootstrapRequest
from app.database import verify_user, create_session, revoke_session, has_users, create_user
from app.core.auth import get_current_user

router = APIRouter(prefix="/api/auth")

@router.get("/status")
async def auth_status():
    return {"has_users": has_users()}

@router.post("/bootstrap")
async def auth_bootstrap(req: BootstrapRequest):
    if has_users():
        return {"status": False, "message": "Already initialized"}
    create_user(req.username.strip(), req.password, "admin")
    return {"status": True}

@router.post("/login")
async def auth_login(req: LoginRequest):
    user = verify_user(req.username.strip(), req.password)
    if not user:
        return {"status": False, "message": "Invalid credentials"}
    token = create_session(user["id"])
    resp = JSONResponse({"status": True, "user": {"username": user["username"], "role": user["role"]}})
    resp.set_cookie("session", token, httponly=True, samesite="lax")
    return resp

@router.get("/me")
async def auth_me(user=Depends(get_current_user)):
    return {"user": {"username": user["username"], "role": user["role"]}}

@router.post("/logout")
async def auth_logout(request: Request):
    token = request.cookies.get("session")
    if token:
        revoke_session(token)
    resp = JSONResponse({"status": True})
    resp.delete_cookie("session")
    return resp
