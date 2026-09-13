"""
In-memory pub/sub for the Agent Activity Log.

Every agent invocation -- whether part of a full /simulate/start run or a
single targeted endpoint call like POST /catalog/refresh -- goes through
`emit()`. That's what makes the log feel alive even when someone is just
clicking one button at a time instead of running the whole pipeline.

SSE (not WebSocket) is used for this stream deliberately: it's one-directional
server->client data, it runs over plain HTTP (survives flaky demo Wi-Fi /
proxies better than a WebSocket upgrade), and the browser's native
EventSource reconnects automatically on drop.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator

from app.state import store

_subscribers: list[asyncio.Queue] = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def emit(agent: str, status: str, detail: str) -> dict:
    ts = _now_iso()
    event = store.append_log(agent=agent, status=status, detail=detail, ts=ts)
    for q in list(_subscribers):
        await q.put(event)
    return event


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    if q in _subscribers:
        _subscribers.remove(q)


async def stream(q: asyncio.Queue) -> AsyncIterator[dict]:
    while True:
        event = await q.get()
        yield event
