from datetime import datetime
from fastapi import WebSocket
from app.state import state
from app.database import append_task_log

class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def log(self, channel: str, text: str):
        if state.current_task_id:
            append_task_log(state.current_task_id, text)
        await self.broadcast({
            "type": "log",
            "channel": channel,
            "text": f"[{datetime.now().strftime('%H:%M:%S')}] {text}",
        })

    async def send_state(self):
        await self.broadcast({"type": "state", "data": state.to_dict()})

manager = ConnectionManager()
