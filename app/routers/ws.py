from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.database import get_user_by_session
from app.core.ws import manager

router = APIRouter()

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    user = get_user_by_session(ws.cookies.get("session"))
    if not user:
        await ws.close(code=1008)
        return
    await manager.connect(ws)
    try:
        await manager.send_state()
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
