from fastapi import Request, Depends, HTTPException
from app.database import get_user_by_session

def get_current_user(request: Request):
    token = request.cookies.get("session")
    user = get_user_by_session(token)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user

def require_admin(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    return user
