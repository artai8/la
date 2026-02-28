from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.ws import manager
from app.state import state
import asyncio

router = APIRouter(prefix="/ws")

@router.websocket("")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # We can push state updates here periodically
            await websocket.send_json(state.to_dict())
            await asyncio.sleep(1)
            # Also read potential incoming messages (ping/pong)
            # await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
