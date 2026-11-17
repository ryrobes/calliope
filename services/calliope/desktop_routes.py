"""Minimal Computer UI API and a revocable KasmVNC stream gateway."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import re
import ssl
from urllib.parse import urlsplit
import uuid

import httpx
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect

import calliope_access
import desktop_access as access
import desktop_tools
import desktop_agents
import desktop_storage
import desktop_memory
import desktop_viewer
from desktop_runtime import DesktopRuntime


BASE = "/api/calliope/computer"

# Keep the protocol-matched Kasm client. Local assets only style its wrapper and
# observe connection state; ownership, reconnect and input stay in the gateway.
VIEWER_SHELL = b"""<link rel="stylesheet" href="/calliope/computer-viewer.css">
<script defer src="/calliope/computer-viewer.js"></script>"""


@dataclass(eq=False)
class Viewer:
    client: object
    upstream: object
    generation: int


def human_event(data):
    """Record gestures, never key values or clipboard/password text.

RFB input commonly arrives as one message per WebSocket frame. We recognize
only exact input shapes, not arbitrary handshake bytes or unknown extensions.
This is a compact gesture trace, not a claim to capture every VNC extension.
"""
    if not isinstance(data, bytes):
        return None
    if len(data) == 6 and data[0] == 5:
        return {"kind": "pointer", "buttons": data[1],
                "x": int.from_bytes(data[2:4], "big"), "y": int.from_bytes(data[4:6], "big")}
    if len(data) == 8 and data[0] == 4 and data[1] in (0, 1) and data[2:4] == b"\0\0":
        return {"kind": "key", "down": bool(data[1]), "value_omitted": True}
    return None


class ComputerGateway:
    def __init__(self, conn_factory, config, owner_for_request):
        self.conn_factory, self.config = conn_factory, config
        self.owner_for_request = owner_for_request
        self.transition = asyncio.Lock()
        self.viewers: set[Viewer] = set()
        host = urlsplit(config.desktop_url).hostname or "desktop"
        self.vnc_base = os.environ.get("CALLIOPE_DESKTOP_VNC_URL", f"https://{host}:6901").rstrip("/")

    def owner(self, request, *, mutation=False, websocket=False):
        actor, session = self.owner_for_request(request)
        if not actor or not session or not session.get("mapped", True):
            raise access.DesktopError("SIGN_IN_REQUIRED", "Sign in to use the computer.", 401)
        if not self.config.desktop_enabled:
            raise access.DesktopError("DESKTOP_DISABLED", "The computer is not enabled.", 404)
        origin = request.headers.get("origin")
        if (mutation or websocket) and origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise access.DesktopError("ORIGIN_MISMATCH", "Use the computer from this installation.", 403)
        if websocket and not origin:
            raise access.DesktopError("ORIGIN_REQUIRED", "A browser origin is required.", 403)
        if mutation and request.headers.get("x-calliope-computer") != "1":
            raise access.DesktopError("MUTATION_HEADER_REQUIRED", "Reload the Computer view.", 403)
        return actor

    async def runtime(self, method, path, payload=None):
        return await asyncio.to_thread(DesktopRuntime(self.config).request, method, path, payload)

    async def disconnect_viewers(self):
        viewers = tuple(self.viewers)
        if viewers:
            await asyncio.gather(*(viewer.upstream.close() for viewer in viewers), return_exceptions=True)
            await asyncio.gather(*(viewer.client.close(code=1012) for viewer in viewers), return_exceptions=True)

    async def state(self, actor):
        state = await self.runtime("GET", "status")
        await asyncio.to_thread(access.sync_runs, self.conn_factory, state)
        active = state.get("runs", {}).get(state.get("active_run"))
        agent = await asyncio.to_thread(desktop_agents.active_agent, self.conn_factory, state.get('active_run'))
        jobs = [{k: v for k, v in job.items() if k in {"id", "run_id", "intent", "status", "started_at", "ended_at", "terminal"}}
                for job in state.get("jobs", {}).values() if job.get("run_id") == state.get("active_run")]
        return {"enabled": True, "active_run": active, "mode": state["mode"],
                "active_agent": agent,
                "pause_reason": state.get("pause_reason"),
                "generation": state["generation"], "queue_length": len(state.get("queue", [])),
                "recording": bool((state.get("recording") or {}).get("recording")),
                "human_actor": state.get("human_actor"), "you_are_driving": state.get("human_actor") == actor,
                "jobs": jobs[-10:], "viewer_url": BASE + "/viewer/"}

    async def change_control(self, actor, body):
        mode = body.get("mode")
        if mode not in {"human", "agent", "paused", "finish"}:
            raise access.DesktopError("INVALID_MODE", "Choose human, agent, paused or finish.")
        async with self.transition:
            state = await self.runtime("GET", "status")
            rid = state.get("active_run")
            if body.get("generation") != state["generation"]:
                raise access.DesktopError("CONTROL_CHANGED", "Control changed. Refresh and try again.", 409)
            if not rid:
                if mode != "human":
                    return await self.state(actor)
                opened = await asyncio.to_thread(desktop_tools.open_desktop, self.conn_factory, self.config,
                                                  body.get("session_id"), actor, "Human demonstration and assistance")
                rid = opened["run_id"]
                if opened["status"] != "active":
                    raise access.DesktopError("DESKTOP_BUSY", "The computer is busy; this task is queued.", 409)
                state = await self.runtime("GET", "status")
            # Freeze execution first. Revoke old sockets before granting the
            # next driver; a cached credential/disabled button is insufficient.
            paused = await self.runtime("POST", "control", {"run_id": rid, "mode": "paused", "actor": actor,
                                                              "expected_generation": state["generation"]})
            await self.disconnect_viewers()
            if mode == "finish":
                await asyncio.to_thread(desktop_agents.fence, self.conn_factory, rid, actor)
                await self.runtime("POST", "finish", {"run_id": rid, "outcome": "Stopped by " + actor})
            else:
                await self.runtime("POST", "control", {"run_id": rid, "mode": mode, "actor": actor,
                                                         "expected_generation": paused["generation"]})
            current = await self.state(actor)
            await asyncio.to_thread(access.append_event, self.conn_factory, rid, actor, "control.changed",
                                    {"mode": mode, "generation": current['generation']}, "control:" + str(uuid.uuid4()))
            if mode == "agent":
                await asyncio.to_thread(access.ready_message, self.conn_factory, rid, current["generation"])
            if mode == "finish":
                await desktop_agents.cancel_pending(self.conn_factory, self.config, rid)
            return current

    async def stream(self, websocket):
        try:
            actor = self.owner(websocket, websocket=True)
        except access.DesktopError:
            await websocket.close(code=1008)
            return
        viewer = None
        tasks = []
        try:
            async with self.transition:
                state = await self.runtime("GET", "status")
                can_control = (state["mode"] == "human" and state.get("human_actor") == actor
                               and websocket.query_params.get("view_only") != "1")
                credential = self.config.desktop_vnc_basic if can_control else self.config.desktop_vnc_view_basic
                if not re.fullmatch(r"Basic [A-Za-z0-9+/]+={0,2}", credential):
                    raise access.DesktopError("DESKTOP_AUTH_INVALID", "Computer stream credentials are invalid.", 503)
                tls = ssl._create_unverified_context() if self.vnc_base.startswith("https:") else None
                upstream = await connect(self.vnc_base.replace("https:", "wss:").replace("http:", "ws:") + "/websockify",
                                         additional_headers={"Authorization": credential}, subprotocols=["binary"],
                                         origin=self.vnc_base,
                                         ssl=tls, max_size=None, open_timeout=10, close_timeout=2,
                                         # This Kasm server does not answer WebSocket pings.
                                         # The library's default 20s ping + 20s timeout
                                         # otherwise kills a healthy RFB stream every 40s.
                                         # Browser-leg keepalive and generation revocation
                                         # are independent of this upstream setting.
                                         ping_interval=None, compression=None, proxy=None)
                await websocket.accept(subprotocol="binary")
                viewer = Viewer(websocket, upstream, state["generation"])
                self.viewers.add(viewer)

            async def to_guest():
                # Capture input only after the client has started requesting
                # framebuffer updates; never interpret authentication bytes.
                input_ready = False
                last_pointer = 0.0
                last_buttons = 0
                loop = asyncio.get_running_loop()
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    data = message.get("bytes") if message.get("bytes") is not None else message.get("text")
                    if data is None:
                        continue
                    if isinstance(data, bytes) and len(data) == 10 and data[0] == 3:
                        input_ready = True
                    event = human_event(data) if input_ready and can_control else None
                    if event:
                        # Pointer moves are useful at modest cadence; clicks
                        # and key transitions retain their original timestamps.
                        now = loop.time()
                        if (event["kind"] != "pointer" or event["buttons"] != last_buttons
                                or now - last_pointer > 0.15):
                            await self.runtime("POST", "human-input", {"run_id": state["active_run"],
                                "generation": viewer.generation, "actor": actor, "event": event})
                            if event["kind"] == "pointer":
                                last_pointer = now
                                last_buttons = event["buttons"]
                    await upstream.send(data)

            async def to_browser():
                async for data in upstream:
                    if isinstance(data, bytes):
                        await websocket.send_bytes(data)
                    else:
                        await websocket.send_text(data)

            async def watch_generation():
                while True:
                    await asyncio.sleep(0.5)
                    current = await self.runtime("GET", "status")
                    if (current["generation"] != viewer.generation or
                            (can_control and (current["mode"] != "human" or current.get("human_actor") != actor))):
                        return

            tasks = [asyncio.create_task(f()) for f in (to_guest, to_browser, watch_generation)]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception as exc:
            # The iframe shows a reconnect state. Never return upstream Basic
            # credentials or an exception containing private connection details.
            print(f"computer stream ended: {type(exc).__name__}", flush=True)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if viewer:
                self.viewers.discard(viewer)
                await viewer.upstream.close()
            try:
                await websocket.close(code=1012)
            except (RuntimeError, WebSocketDisconnect):
                pass

    async def viewer_asset(self, request):
        self.owner(request)
        asset = request.path_params.get("asset", "") or "vnc.html"
        if ".." in asset.split("/") or asset.startswith("/"):
            raise access.DesktopError("NOT_FOUND", "Unknown viewer asset.", 404)
        if asset == "disconnected.html":
            return Response(desktop_viewer.DISCONNECTED_HTML, media_type="text/html",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        if asset != "vnc.html" and not asset.startswith(("assets/", "app/", "core/")):
            raise access.DesktopError("NOT_FOUND", "Unknown viewer asset.", 404)
        async with httpx.AsyncClient(timeout=30, verify=False, trust_env=False) as client:
            response = await client.get(self.vnc_base + "/" + asset,
                                        headers={"Authorization": self.config.desktop_vnc_view_basic})
        if response.status_code != 200:
            return Response(status_code=404)
        content = response.content
        if asset == "vnc.html":
            content = desktop_viewer.adapt_html(content)
            content = content.replace(b"</head>", VIEWER_SHELL + b"</head>", 1)
        elif desktop_viewer.UI_ASSET.fullmatch(asset):
            content = desktop_viewer.adapt_ui(content)
        return Response(content, media_type=response.headers.get("content-type", "application/octet-stream"),
                        headers={"Cache-Control": "no-store" if asset == "vnc.html" else "private, max-age=300",
                                 "X-Content-Type-Options": "nosniff"})

    async def route(self, request):
        try:
            actor = self.owner(request, mutation=request.method == "POST")
            suffix = request.url.path[len(BASE):]
            if suffix.startswith("/viewer/"):
                return await self.viewer_asset(request)
            if request.method == "GET" and suffix == "/status":
                return JSONResponse(await self.state(actor), headers={"Cache-Control": "no-store"})
            if request.method == "GET" and suffix == "/runs":
                sid = access.uid(request.query_params.get("session_id"))
                offset = max(0, int(request.query_params.get('offset', 0)))
                def read_runs():
                    with self.conn_factory() as conn:
                        calliope_access.require_participant(conn, sid, actor)
                        return [access.serial(row) for row in conn.execute(
                            "SELECT r.*,coalesce((SELECT (e.payload->>'keep')::boolean "
                            "FROM rvbbit.calliope_workbench_events e WHERE e.desktop_run_id=r.id "
                            "AND e.event_type='recording.retention' ORDER BY e.sequence DESC LIMIT 1),false) AS keep_recording "
                            "FROM rvbbit.calliope_desktop_runs r WHERE session_id=%s::uuid ORDER BY created_at DESC,id DESC LIMIT 31 OFFSET %s", (sid, offset)
                        ).fetchall()]
                runs = await asyncio.to_thread(read_runs)
                return JSONResponse({"runs": runs[:30], "next_offset": offset + 30 if len(runs) > 30 else None},
                                    headers={"Cache-Control": "private, no-store"})
            if request.method == "GET" and suffix.startswith("/runs/"):
                return await self.run_evidence(request, actor)
            if request.method == "POST":
                raw = await request.body()
                if len(raw) > 64000:
                    raise access.DesktopError("REQUEST_TOO_LARGE", "Computer request is too large.", 413)
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise access.DesktopError("INVALID_REQUEST", "Expected an object.")
                if suffix == "/control":
                    return JSONResponse(await self.change_control(actor, body))
                if suffix == "/retention":
                    return JSONResponse(await asyncio.to_thread(desktop_storage.set_keep, self.conn_factory,
                        self.config, body.get('session_id'), body.get('run_id'), actor, body.get('keep')))
                if suffix == "/methods/forget":
                    import desktop_learning
                    return JSONResponse(await asyncio.to_thread(desktop_learning.retire, self.conn_factory,
                        body.get('session_id'), actor, body.get('run_id'), body.get('method_id')))
                if suffix == "/feedback":
                    async with self.transition:
                        generation = body.get('human_generation')
                        if generation is not None:
                            state = await self.runtime('GET', 'status')
                            if (isinstance(generation, bool) or not isinstance(generation, int)
                                    or state['generation'] != generation or state['mode'] != 'human'
                                    or state.get('human_actor') != actor or state.get('active_run') != body.get('run_id')):
                                raise access.DesktopError('CONTROL_CHANGED', 'Control changed. Add feedback from History.', 409)
                        result = await asyncio.to_thread(desktop_memory.save_feedback, self.conn_factory,
                            body.get('session_id'), actor, body.get('run_id'), body.get('feedback'),
                            body.get('request_id'), body.get('demonstration_id'), generation)
                    return JSONResponse(result, status_code=202)
            return JSONResponse({"error": {"code": "NOT_FOUND"}}, status_code=404)
        except (access.DesktopError, calliope_access.CalliopeAccessError) as exc:
            return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status)
        except (ValueError, TypeError):
            return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": "Invalid computer request."}}, status_code=400)
        except Exception:
            return JSONResponse({"error": {"code": "COMPUTER_UNAVAILABLE", "message": "The computer is reconnecting. Try again shortly."}}, status_code=503)

    async def run_evidence(self, request, actor):
        parts = request.url.path[len(BASE + "/runs/"):].split("/")
        rid = access.uid(parts[0])
        sid = access.uid(request.query_params.get("session_id"))
        run = await asyncio.to_thread(access.run_for_session, self.conn_factory, sid, actor, rid)
        tail = "/".join(parts[1:])
        if tail == 'thumbnail.jpg':
            import desktop_replay
            image = await asyncio.to_thread(desktop_replay.thumbnail, self.conn_factory, self.config, rid)
            return Response(image, media_type='image/jpeg',
                            headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
        if re.fullmatch(r'jobs/[0-9a-f-]{36}/terminal', tail):
            jid = access.uid(parts[2])
            offset = max(0, int(request.query_params.get('offset', 0)))
            import desktop_evidence
            from desktop_terminal import read_page
            folder = desktop_evidence.directory(self.config, rid) / 'jobs' / jid
            if (folder / 'archived.json').exists():
                job = json.loads((folder / 'archived.json').read_text())
                if not job.get('terminal'):
                    raise access.DesktopError('NOT_FOUND', 'This job has no terminal recording.', 404)
                return JSONResponse({**await asyncio.to_thread(read_page, folder / 'terminal.cast', offset),
                    'complete': True, 'status': job['status'], 'intent': job['intent'],
                    'code': (folder / 'code.sh').read_text()}, headers={'Cache-Control': 'private, no-store'})
            runtime = DesktopRuntime(self.config)
            try:
                job = await asyncio.to_thread(runtime.request, 'GET', 'jobs/' + jid)
            except Exception:
                snapshot = folder / 'terminal-state.json'
                if not snapshot.exists():
                    raise
                job = json.loads(snapshot.read_text())
                return JSONResponse({**await asyncio.to_thread(read_page, folder / 'terminal.cast', offset),
                    'complete': True, 'offline': True, 'status': 'last saved output · guest unavailable',
                    'intent': job['intent'], 'code': job['code']}, headers={'Cache-Control': 'private, no-store'})
            if job.get('run_id') != rid or not job.get('terminal'):
                raise access.DesktopError('NOT_FOUND', 'This terminal does not belong to this task.', 404)
            data = await asyncio.to_thread(runtime.request, 'GET', f'terminal/{jid}?offset={offset}')
            return JSONResponse(data, headers={'Cache-Control': 'private, no-store'})
        if tail == 'demonstrations':
            def read_demonstrations():
                with self.conn_factory() as conn:
                    return desktop_memory.demonstrations(conn, run)
            return JSONResponse({'demonstrations': await asyncio.to_thread(read_demonstrations)},
                                headers={'Cache-Control': 'no-store'})
        if tail == "receipts":
            return JSONResponse(await asyncio.to_thread(access.receipts, self.conn_factory, sid, actor, rid,
                int(request.query_params.get('after', 0)), request.query_params.get('summary') == '1'))
        if tail == "events":
            key = f"computer/runs/{rid}/events.jsonl"
        elif tail == "manifest":
            key = f"computer/runs/{rid}/video/manifest.json"
        elif re.fullmatch(r"video/(?:\d{6}|replay)\.mp4", tail):
            key = f"computer/runs/{rid}/" + tail
        elif re.fullmatch(r"jobs/[0-9a-f-]{36}/(?:code\.py|code\.sh|stdout\.txt|stderr\.txt|terminal\.cast)", tail):
            access.uid(parts[2])
            key = f"computer/runs/{rid}/" + tail
        else:
            raise access.DesktopError("NOT_FOUND", "Unknown computer evidence.", 404)
        # Prefer finalized appliance copies; history works even with the guest
        # stopped. Live event journals and manifests still come from the guest.
        import desktop_evidence
        archive = desktop_evidence.directory(self.config, rid)
        manifest_path = archive / 'manifest.json'
        if tail.startswith('video/') and manifest_path.is_file() and json.loads(manifest_path.read_text()).get('expired_at'):
            raise access.DesktopError('RECORDING_EXPIRED', 'This recording expired; task receipts are still available.', 410)
        local = archive / ({"events": "events.jsonl", "manifest": "manifest.json"}.get(tail, tail))
        if tail == 'manifest' and local.is_file() and run['status'] in {'finished', 'interrupted', 'failed'}:
            value = json.loads(local.read_text())
            replay_path = archive / 'replay.json'
            if not value.get('expired_at'):
                value['replay'] = json.loads(replay_path.read_text()) if replay_path.is_file() else {'status': 'pending'}
            return JSONResponse(value, headers={"Cache-Control": "private, no-store"})
        if tail == 'video/replay.mp4':
            replay_path = archive / 'replay.json'
            if not replay_path.is_file() or json.loads(replay_path.read_text()).get('status') != 'ready' or not local.is_file():
                raise access.DesktopError('EVIDENCE_PENDING', 'The single-session replay is still being prepared.', 404)
            return FileResponse(local, media_type='video/mp4', headers={"Cache-Control": "private, no-store"})
        if local.is_file() and (tail.startswith(("video/", "jobs/")) or run["status"] in {"finished", "interrupted", "failed"}):
            return FileResponse(local, headers={"Cache-Control": "private, no-store"})
        runtime = DesktopRuntime(self.config)
        client = httpx.AsyncClient(timeout=120, trust_env=False)
        headers = dict(runtime.headers)
        if request.headers.get("range"):
            headers["Range"] = request.headers["range"]
        try:
            response = await client.send(client.build_request("GET", runtime.base + "/computer/files/" + key, headers=headers), stream=True)
        except BaseException as exc:
            await client.aclose()
            if isinstance(exc, Exception) and local.is_file():
                return FileResponse(local, headers={"Cache-Control": "private, no-store", "X-Computer-Evidence": "archived-snapshot"})
            raise
        if response.is_error:
            await response.aclose()
            await client.aclose()
            if local.is_file():
                return FileResponse(local, headers={"Cache-Control": "private, no-store", "X-Computer-Evidence": "archived-snapshot"})
            raise access.DesktopError("EVIDENCE_PENDING", "This evidence is not finalized yet.", 404)
        async def content():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
        passthrough = {k: v for k, v in response.headers.items() if k.lower() in {"content-length", "content-range", "accept-ranges"}}
        return StreamingResponse(content(), status_code=response.status_code,
                                 media_type=response.headers.get("content-type"), headers={**passthrough, "Cache-Control": "no-store"})


def register(mcp, conn_factory, config, owner_for_request):
    gateway = ComputerGateway(conn_factory, config, owner_for_request)
    mcp._calliope_computer_gateway = gateway
    from desktop_decisions import Decisions
    mcp.custom_route(BASE + "/decide", methods=["POST"])(Decisions(gateway).route)
    if config.desktop_enabled:
        import desktop_browser_auth
        desktop_browser_auth.start_worker(conn_factory, config)
        import desktop_memory
        import desktop_evidence
        desktop_memory.start_worker(conn_factory)
        desktop_evidence.start_worker(conn_factory, config)
        import desktop_learning
        desktop_learning.start_worker(conn_factory, config)
    for path, methods in (("/status", ["GET"]), ("/control", ["POST"]), ("/feedback", ["POST"]), ("/retention", ["POST"]), ("/methods/forget", ["POST"]),
                          ("/runs", ["GET"]), ("/runs/{run_id}/{evidence:path}", ["GET"]),
                          ("/viewer/", ["GET"]), ("/viewer/{asset:path}", ["GET"])):
        mcp.custom_route(BASE + path, methods=methods)(gateway.route)


def attach_websocket(app, mcp):
    gateway = getattr(mcp, "_calliope_computer_gateway", None)
    if gateway:
        app.router.routes.append(WebSocketRoute(BASE + "/stream", gateway.stream))
    return app
