"""HTTP + WebSocket front end for the village.

Binds to loopback only. The event stream carries prompt text, file paths and branch names from
real work repos, and none of that should leave the machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .control import Refused, check_request
from .daemon import Village, VillageConfig

log = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
WEB_PUBLIC = Path(__file__).resolve().parents[2] / "web" / "public"


class Hub:
    """Fan out world snapshots to every connected client."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def join(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def leave(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        if not self._clients:
            return
        message = json.dumps(payload)
        for ws in list(self._clients):
            try:
                await ws.send_text(message)
            except Exception:  # noqa: BLE001 - any send failure means that client is gone
                self.leave(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)


def create_app(config: VillageConfig | None = None, *, max_fps: float = 10.0) -> FastAPI:
    village = Village(config)
    hub = Hub()
    app = FastAPI(title="Agent Village", docs_url=None, redoc_url=None)
    app.state.village = village
    app.state.hub = hub
    # Fresh per daemon start. Nothing persists it, so a token cannot outlive the process it
    # was minted for.
    app.state.control_token = secrets.token_urlsafe(32)

    async def pump() -> None:
        interval = village.config.poll_interval_s
        min_frame = 1.0 / max_fps
        last_frame = 0.0
        while True:
            try:
                changed = await asyncio.to_thread(village.poll_once)
                now = asyncio.get_running_loop().time()
                # Always refresh at least once a second so "time in state" counters keep ticking
                # even while nothing happens.
                if (changed or now - last_frame > 1.0) and now - last_frame >= min_frame:
                    await hub.broadcast({"type": "world", **village.snapshot()})
                    last_frame = now
            except Exception:
                log.exception("village poll failed")
            await asyncio.sleep(interval)

    @app.on_event("startup")
    async def _startup() -> None:
        await asyncio.to_thread(village.backfill)
        app.state.pump = asyncio.create_task(pump())

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        task = getattr(app.state, "pump", None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @app.get("/api/world")
    async def world() -> JSONResponse:
        return JSONResponse(village.snapshot())

    @app.get("/api/agents/{session_id}")
    async def agent_detail(session_id: str) -> JSONResponse:
        data = village.detail(session_id)
        if data is None:
            return JSONResponse({"error": "no such agent"}, status_code=404)
        return JSONResponse(data)

    # -- control -------------------------------------------------------------------------
    #
    # The only endpoints that change anything. `/api/session` hands the page its token; a
    # cross-origin script can issue that GET but cannot read the reply, so the token stays put.

    @app.get("/api/session")
    async def session() -> JSONResponse:
        return JSONResponse({"token": app.state.control_token})

    def guard(request: Request) -> None:
        check_request(
            token=app.state.control_token,
            supplied_token=request.headers.get("x-village-token"),
            host=request.headers.get("host"),
            origin=request.headers.get("origin"),
            content_type=request.headers.get("content-type"),
        )

    @app.post("/api/agents/{session_id}/stop")
    async def stop_agent(session_id: str, request: Request) -> JSONResponse:
        try:
            guard(request)
            pid = village.stop_agent(session_id)
        except Refused as refused:
            return JSONResponse({"error": refused.reason}, status_code=refused.status)
        return JSONResponse({"ok": True, "pid": pid, "signalled": session_id})

    @app.post("/api/agents/{session_id}/prompt")
    async def prompt_agent(session_id: str, request: Request) -> JSONResponse:
        try:
            guard(request)
            body = await request.json()
            pane = village.prompt_agent(session_id, str(body.get("text") or ""))
        except Refused as refused:
            return JSONResponse({"error": refused.reason}, status_code=refused.status)
        except ValueError:
            return JSONResponse({"error": "Expected a JSON body."}, status_code=400)
        return JSONResponse({"ok": True, "pane": pane})

    @app.post("/api/agents/{session_id}/dismiss")
    async def dismiss_agent(session_id: str, request: Request) -> JSONResponse:
        try:
            guard(request)
            village.dismiss_agent(session_id)
        except Refused as refused:
            return JSONResponse({"error": refused.reason}, status_code=refused.status)
        await hub.broadcast({"type": "world", **village.snapshot()})
        return JSONResponse({"ok": True, "dismissed": session_id})

    @app.post("/api/agents/{session_id}/rename")
    async def rename_agent(session_id: str, request: Request) -> JSONResponse:
        try:
            guard(request)
            body = await request.json()
            title = body.get("title") if isinstance(body, dict) else None
            if not isinstance(title, str):
                return JSONResponse({"error": "Expected a JSON body with a title."}, status_code=400)
            village.rename_agent(session_id, title)
        except Refused as refused:
            return JSONResponse({"error": refused.reason}, status_code=refused.status)
        except ValueError:
            return JSONResponse({"error": "Expected a JSON body."}, status_code=400)
        await hub.broadcast({"type": "world", **village.snapshot()})
        return JSONResponse({"ok": True, "renamed": session_id})

    @app.get("/api/history")
    async def history(limit: int = 500) -> JSONResponse:
        if not village.store:
            return JSONResponse({"transitions": []})
        return JSONResponse({"transitions": village.store.recent_transitions(limit)})

    @app.get("/api/usage")
    async def usage() -> JSONResponse:
        return JSONResponse(village.usage())

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "agents": len(village.world.agents),
                "clients": hub.client_count,
                "spoolDropped": village.spool.dropped,
            }
        )

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await hub.join(ws)
        try:
            await ws.send_text(json.dumps({"type": "world", **village.snapshot()}))
            while True:
                await ws.receive_text()  # clients are read-only; this just detects disconnects
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001, S110 - a dead socket is normal; the finally clause is the whole handling
            pass
        finally:
            hub.leave(ws)

    # The debug table is the phase-1 checkpoint and stays available after the 3D world exists:
    # when something looks wrong in the world, the table is how you tell whether the bug is in
    # the data or in the rendering.
    @app.get("/table")
    async def table() -> FileResponse:
        return FileResponse(WEB_PUBLIC / "table.html")

    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="world")
    else:

        @app.get("/")
        async def no_build() -> FileResponse:
            return FileResponse(WEB_PUBLIC / "table.html")

    return app
