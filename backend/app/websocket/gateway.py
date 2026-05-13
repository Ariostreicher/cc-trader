"""WebSocket gateway — live quotes + alert notifications.

Clients subscribe to channels per symbol. The server polls quotes on a short
loop and pushes updates; in a future iteration this will switch to broker
websocket fan-in via Redis pubsub.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from typing import Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from ..core.security import decode_token
from ..services.market.registry import MarketDataService

ws_router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)
        self.user_connections: dict[uuid.UUID, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, user_id: uuid.UUID) -> None:
        await ws.accept()
        async with self._lock:
            self.user_connections[user_id].add(ws)

    async def disconnect(self, ws: WebSocket, user_id: uuid.UUID) -> None:
        async with self._lock:
            for subscribers in self.connections.values():
                subscribers.discard(ws)
            if user_id in self.user_connections:
                self.user_connections[user_id].discard(ws)

    async def subscribe(self, ws: WebSocket, symbol: str) -> None:
        async with self._lock:
            self.connections[symbol.upper()].add(ws)

    async def unsubscribe(self, ws: WebSocket, symbol: str) -> None:
        async with self._lock:
            self.connections[symbol.upper()].discard(ws)

    async def broadcast_symbol(self, symbol: str, payload: dict) -> None:
        targets: Set[WebSocket] = set()
        async with self._lock:
            targets = set(self.connections.get(symbol.upper(), set()))
        for ws in targets:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                pass

    async def push_to_user(self, user_id: uuid.UUID, payload: dict) -> None:
        async with self._lock:
            targets = set(self.user_connections.get(user_id, set()))
        for ws in targets:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                pass

    def subscribed_symbols(self) -> list[str]:
        return [s for s, subs in self.connections.items() if subs]


manager = ConnectionManager()


@ws_router.websocket("/ws/stream")
async def stream(websocket: WebSocket, token: str = Query(...)):
    # Auth handshake.
    try:
        payload = decode_token(token)
        if payload.get("kind") != "access":
            raise ValueError("wrong token kind")
        user_id = uuid.UUID(payload["sub"])
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket, user_id)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"error": "invalid json"}))
                continue

            action = msg.get("action")
            symbol = (msg.get("symbol") or "").upper()
            if action == "subscribe" and symbol:
                await manager.subscribe(websocket, symbol)
                await websocket.send_text(json.dumps({"ok": True, "subscribed": symbol}))
            elif action == "unsubscribe" and symbol:
                await manager.unsubscribe(websocket, symbol)
                await websocket.send_text(json.dumps({"ok": True, "unsubscribed": symbol}))
            elif action == "ping":
                await websocket.send_text(json.dumps({"pong": True}))
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket, user_id)


async def quote_pump_loop(interval: float = 5.0) -> None:
    """Background task: poll quotes for all subscribed symbols and broadcast."""
    while True:
        symbols = manager.subscribed_symbols()
        if symbols:
            quotes = await MarketDataService.quotes(symbols)
            for sym, q in quotes.items():
                await manager.broadcast_symbol(
                    sym,
                    {
                        "type": "quote",
                        "symbol": sym,
                        "price": q.price,
                        "change": q.change,
                        "change_pct": q.change_pct,
                        "ts": q.timestamp.isoformat(),
                    },
                )
        await asyncio.sleep(interval)
