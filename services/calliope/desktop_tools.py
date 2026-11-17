"""Computer operations shared by agent tools and the Stage endpoints."""
from pathlib import Path
import time
import uuid

import calliope_access
import desktop_access as access
import desktop_memory
import desktop_learning
import desktop_agents
import desktop_browser_auth
import shared_files
from desktop_runtime import DesktopRuntime


def _state(conn_factory, runtime):
    state = runtime.request("GET", "status")
    access.sync_runs(conn_factory, state)
    return state


def open_desktop(conn_factory, config, session_id, actor, goal, controller=None):
    runtime = DesktopRuntime(config)
    # Reconcile interrupted/finished runs before deciding whether to reuse one.
    _state(conn_factory, runtime)
    run = access.open_run(conn_factory, session_id, actor, goal)
    desktop_agents.attach(conn_factory, run['id'], actor, controller)
    value = runtime.request("POST", "open", {"run_id": run["id"], "context": {
        "session_id": str(session_id), "requester": actor, "goal": run["goal"],
    }})
    access.sync_runs(conn_factory, {"runs": {run["id"]: value}})
    access.append_event(conn_factory, run["id"], actor, "run.opened", value, "run.opened")
    browser_auth = (desktop_browser_auth.prepare(conn_factory, config, runtime)
                    if value['status'] == 'active' else {'status': 'queued'})
    shared_delivery = shared_files.sync(config, str(session_id))
    return {"desktop_id": run["desktop_id"], "run_id": run["id"],
            "shared_folder": shared_files.desktop_folder(session_id), "shared_files": shared_delivery,
            "browser_auth": browser_auth,
            "status": value["status"], "goal": run["goal"],
            "lessons": desktop_memory.recall(conn_factory, run, actor),
            "methods": desktop_learning.recall(conn_factory, run, actor),
            "viewer_url": "/api/calliope/computer/viewer/",
            "instructions": (
                "Use desktop_exec for Python or shell in this persistent computer. Batch a coherent subtask: observe, act and verify in one job; report milestones or exceptions, not every click. "
                "The methods field contains relevant prior working recipes with evidence-assessed outcome counts. Adapt matching methods, reacquire current targets, and check actual results; human corrections take precedence. No extra reporting calls are needed for learning. "
                "Python: from calliope_computer import cua, browser, browser_page, browser_diagnostics. cua.tools() shows the eight starting operations; tools(query=...) or tools(group=...) discovers more. "
                "cua.describe(name) returns compact JSON arguments/examples/warnings; full=True gives the full schema/docs. cua.call(name, arguments) executes. "
                "WEB DEFAULT: with browser() as b: page = browser_page(b, url). This opens/reuses a visible tab in the existing authenticated Chrome/profile and checks navigation. Use browser_auth.origin for this appliance's published URLs. "
                "Use exact DOM locators and assertions for known browser steps. The host browser_exec is a different browser. Use Cua for native dialogs/browser chrome or when DOM access is unavailable. "
                "This installation's published apps authenticate automatically as the Computer task owner; no human login or credential handling is needed. External websites keep their own login sessions. "
                "Do not use new_context(), a separate browser, ignore_https_errors or certificate bypasses to recover login/TLS failures. browser_page retries a certificate-date failure once with verification enabled, then reports the blocker. "
                "If a map/canvas is blank or clicks have no effect, print(browser_diagnostics(page)) and check rendering before trying more coordinates. WebGL failure is a browser fault, not an empty dataset or missed click. Console/resource errors and a canvas element alone do not prove a rendered map; verify loaded layers and an actual selection/release. "
                "NATIVE DEFAULT: list_windows, then cua.map(pid=..., window_id=...) for a compact screen map. It skips document cells and prioritizes the active dialog. "
                "For Calc menus/save confirmations and native dialog chains, first try cua.work(goal='one concrete UI subtask', pid=..., window_id=..., expect=[...]). Omit expect if no predicate is available, but then verify the document/file result yourself. "
                "work uses bounded accessibility, groups candidates for Jev and runs at most four inputs, returning verified, progress, or needs_attention with steps and the screen. It stops on no change or unclear state; do not repeat the same failed work call. "
                "work distinguishes observation_unavailable from a changed screen and automatically retries a failed read once. Its health field checks application responsiveness. "
                "On repeated no-effect input use cua.health(pid=..., window_id=...). An unresponsive app needs recovery, not more clicks, full-tree queries or X11/overlay diagnostics. "
                "Preserve unsaved work unless discard/restart is already authorized. Otherwise report the concrete application blocker promptly; do not ask for Take over as a generic fix. "
                "DriverError means the input failed or its delivery is uncertain; inspect its code and evidence. invalid_action_target is an argument error, not a successful click. "
                "Use cua.describe('work') for the contract. Type explicit paths/values directly, then resume the subtask. For known targets use click/type_text with fresh element_token or observed coordinates and verify_state. "
                "Window observations default to text only. Trees expose controls, not necessarily terminal/canvas/document content; no query match does not prove absence. Use look_at_desktop for a fresh image when structure is insufficient. "
                "For a routine native choice with several plausible controls, use cua.act(goal=..., pid=..., window_id=..., query=..., expect=[...]). "
                "It builds candidates, asks Jev, refreshes the target, clicks once and checks verify_state predicates inside this job. provider='cua-s1', visual=True uses the Qwen visual chooser instead. "
                "act raises ActionNeedsAttention on drift, missing targets, chooser failure or unmet expectations: inspect its evidence and reobserve; never blindly retry. Without expect it returns acted_unverified, not success. "
                "Prefer direct operations for known targets. Read cua.describe('act') for a complete example. Ordinary Python owns iteration; no extra task planner is required. "
                "A browser subtask example: with browser() as b, select the existing page, click an exact button, assert the resulting text/count, and print the result in the same job. "
                "Code may combine GUI actions, files, APIs and office libraries. When the user asks to open, inspect or review a file on the desktop, do that visible step and verify rendered content. "
                "For native dialogs use cua.observe(pid=..., window_id=...). It follows related dialog layers and returns controls with click_arguments, using localized OCR only when accessibility is sparse. OCR text is marked and clickability is unproven; coordinates are already screen-absolute. "
                "After a click, call cua.observe(..., previous=state): a new confirmation is progress, not a failed click. Read its question and target the top dialog; do not repeatedly search the blocked parent tree. "
                "Gracefully close office documents before external edits/reopening. Do not routinely pkill soffice or delete lock/recovery files: that creates recovery dialogs and can lose unsaved work. "
                "A launch receipt or matching window title proves a window exists, not that the document rendered or the application responds. Use look_at_desktop or a content-level check before claiming it is open for review. "
                "You already have permission to operate this task desktop in agent mode. Do not ask for Take over/Let Callie work unless a person actually owns control or must perform a human-only step. Recording faults are system faults, not approval requests. "
                "Open persistent GUI apps with cua.call('launch_app', {'name': 'libreoffice', 'additional_arguments': ['--writer', '/home/kasm-user/document.odt']}). "
                "Use cua.tools()/cua.describe('launch_app') for exact capabilities. Apps launched this way belong to the desktop and stay open after the job. "
                "Do not launch persistent GUI apps with shell &, nohup, disown or Python Popen: those children stay in the job and are stopped at its timeout. "
                "desktop_exec waits up to wait_seconds (default 20) and returns early on completion. Longer jobs run in the background; poll with next_poll, using independent stdout_offset/stderr_offset. Do not use shell sleeps for polling. "
                "language='shell' opens a read-only live terminal in Computer and records timed output for replay. "
                "Use that for installs, file operations and long commands when a shell materially helps. "
                "For browser/app interaction and visual testing, keep using the visible desktop/browser. "
                "Use noninteractive shell commands; stdin is closed. sudo -n is available only when enabled for this dedicated desktop. "
                "Installed system packages survive restarts but container replacement requires preserving or rebuilding its image. "
                "Optional: from calliope_computer import choose; choose(goal=..., observation=..., "
                "options=[{id,role,label,action},...], provider='jev') selects an ID without executing it. Jev is the GPU-free default; provider='cua-s1' explicitly selects the optional Qwen/Cua worker. "
                "Cua-S1 also accepts screenshot=PNG_bytes. Reobserve when uncertain; the chooser is optional, "
                "and its candidate probabilities are not verified task outcomes. "
                "If queued, wait for the current task; do not take over another run. "
                "Call finish_desktop with the observed outcome when this task is done."
            )}


def look_at_desktop(conn_factory, config, session_id, actor, controller=None):
    with conn_factory() as conn:
        calliope_access.require_participant(conn, access.uid(session_id), actor)
    runtime = DesktopRuntime(config)
    result = runtime.request("POST", "observe", {})
    state, shot = result["state"], result["screenshot"]
    access.sync_runs(conn_factory, state)
    # A remote VM does not share the server's filesystem. Fetch the image and
    # verify the guest's digest before the MCP wrapper attaches it to the model.
    key = str(shot["object_key"])
    path = Path(config.file_root) / "computer-captures" / Path(key).name
    runtime.download(key, path, shot.get("sha256"))
    rid = state.get("active_run")
    if rid:
        access.append_event(conn_factory, rid, actor, "observation.captured", shot, "look:" + path.stem)
    active = state.get("runs", {}).get(rid)
    return {"path": str(path), "screenshot": shot, "mode": state["mode"],
            "pause_reason": state.get("pause_reason"),
            "active_run": active, "queue_length": len(state.get("queue", []))}


def desktop_exec(conn_factory, config, session_id, actor, *, code="", intent="",
                 language="python", job_id=None, request_id=None, timeout=600, offset=0, stdout_offset=None, stderr_offset=0, wait_seconds=20, pause_reason="", controller=None):
    runtime = DesktopRuntime(config)
    wait_seconds = max(0, min(20, float(wait_seconds)))
    out_offset = max(0, int(offset if stdout_offset is None else stdout_offset))
    err_offset = max(0, int(stderr_offset))
    def read_job(jid):
        return runtime.request("GET", "jobs/" + access.uid(jid) +
                               f"?stdout_offset={out_offset}&stderr_offset={err_offset}")
    if pause_reason:
        if code or job_id:
            raise access.DesktopError("INVALID_PAUSE", "Pause separately from execution or polling.")
        run = access.run_for_session(conn_factory, session_id, actor)
        desktop_agents.attach(conn_factory, run['id'], actor, controller)
        state = runtime.request("GET", "status")
        if state.get("mode") == "human":
            raise access.DesktopError("HUMAN_CONTROL", "The person already controls this computer.")
        runtime.request("POST", "control", {"run_id": run["id"], "mode": "paused", "actor": actor,
                                              "expected_generation": state["generation"]})
        import calliope
        item = calliope.publish_work_item(conn_factory, session_id, "blocked", "Computer needs your help",
            summary=str(pause_reason)[:4000], source="computer", dedupe_key="computer:" + run["id"],
            action_prompt="Open Computer, take over to help, then choose Let Callie work to resume from the current screen.",
            context={"computer_run_id": run["id"], "goal": run["goal"], "reason": str(pause_reason)[:4000],
                     "viewer_url": "/calliope?session=" + str(session_id), "input_request_id": None})
        access.append_event(conn_factory, run["id"], actor, "assistance.requested",
                            {"reason": pause_reason, "work_item_id": item.get("id")}, "assistance:" + str(uuid.uuid4()))
        return {"status": "paused", "run_id": run["id"], "work_item_id": item.get("id"),
                "next": "End this turn explaining the handoff. Let Callie work sends a continuation to this same task."}
    if job_id:
        job = read_job(job_id)
        run = access.run_for_session(conn_factory, session_id, actor, job["run_id"])
    else:
        run = access.run_for_session(conn_factory, session_id, actor)
        access.require_run_actor(run, actor)
        desktop_agents.attach(conn_factory, run['id'], actor, controller)
        shared_files.sync(config, str(session_id))
        browser_auth = desktop_browser_auth.prepare(conn_factory, config, runtime)
        if browser_auth.get('status') == 'pending' and language != 'shell':
            raise access.DesktopError('COMPUTER_AUTH_PENDING',
                                      'Chrome sign-in is starting or reconnecting. Retry shortly; no code was executed. Shell jobs remain available for browser repair.', 503)
        jid = access.uid(request_id) if request_id else str(uuid.uuid4())
        # Persist intent/code before dispatch, even if the network response is
        # lost. Retrying this request_id polls/reuses the same guest job.
        access.append_event(conn_factory, run["id"], actor, "execution.requested",
                            {"job_id": jid, "intent": intent, "language": language, "code": code}, "request:" + jid)
        job = runtime.request("POST", "execute", {"run_id": run["id"], "job_id": jid,
                              "code": code, "intent": intent, "language": language, "timeout": timeout})
    # Wait in this call instead of requiring an agent-side sleep tool. Never
    # hold the guest's control lock: human takeover can cancel the job promptly.
    deadline = time.monotonic() + wait_seconds
    while job["status"] == "running" and time.monotonic() < deadline:
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        job = read_job(job["id"])
    job["next_poll"] = {"session_id": str(session_id), "job_id": job["id"],
                        "stdout_offset": job.get("stdout_next_offset", out_offset),
                        "stderr_offset": job.get("stderr_next_offset", err_offset), "wait_seconds": 20}
    if job["status"] != "running":
        access.append_event(conn_factory, run["id"], actor, "execution.finished", job, "result:" + job["id"])
    # The submitted code is already in the tool invocation and durable receipt.
    return {k: v for k, v in job.items() if k != "code"}


def finish_desktop(conn_factory, config, session_id, actor, outcome, controller=None):
    runtime = DesktopRuntime(config)
    run = access.run_for_session(conn_factory, session_id, actor)
    desktop_agents.attach(conn_factory, run['id'], actor, controller)
    result = runtime.request("POST", "finish", {"run_id": run["id"], "outcome": outcome})
    _state(conn_factory, runtime)
    access.append_event(conn_factory, run["id"], actor, "run.finished", result, "run.finished")
    return {"run_id": run["id"], "status": result["status"], "outcome": outcome}
