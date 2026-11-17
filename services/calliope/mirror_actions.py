"""Ordinary-chat entry points for the existing managed SQL mirror controller.

Credentials use the native one-way action form. Jobs use durable action receipts,
default to manual execution, and never replace another job as a side effect.
"""
import json
import connection_forms
from contextlib import nullcontext
from urllib.parse import quote


def catalog():
    import calliope as c

    common = dict(version=1, category="admin", risk="reversible", requirements=[],
                  prompt_template="", sort_order=175, state="ready", state_label="Available",
                  config={"requires_admin": True}, tags=["database", "source", "connection", "mirror", "dlt"])
    return [
        {**common, "id": "mirror.connect", "executor": "mirror_connect",
         "title": "Connect database source", "summary": "Connect a SQL source for managed mirrors from any chat.",
         "description": "Connect or repair a source through a connector-defined secure form, including SSH, certificates and driver options. Credentials never belong in chat.",
         "fields": [
             {"key": "connection_name", "label": "Connection name", "type": "text", "required": True},
             {"key": "label", "label": "Label", "type": "text", "required": True},
             {"key": "dialect", "label": "Database type", "type": "select", "required": True,
              "options": [{"value": d, "label": d} for d in sorted(c._SETUP_MIRROR_DIALECTS)]},
             {"key": "secret:SOURCE_DSN", "secret_name": "SOURCE_DSN", "label": "Read-only source SQLAlchemy DSN",
              "type": "secret", "required": True},
             {"key": "secret:CONNECTION_INPUTS", "secret_name": "CONNECTION_INPUTS", "label": "Secure connection details", "type": "secret"},
             {"key": "replace_credentials", "label": "Update the saved connection", "type": "boolean", "default": False},
         ]},
        {**common, "id": "mirror.create", "executor": "mirror_create",
         "title": "Create managed SQL mirror", "summary": "Create a manual mirror from a configured SQL source, optionally running it once.",
         "description": "Use discover_mirror_source for table names and keys. No recurring schedule is created. Repeating identical configuration reuses the job without another run.",
         "fields": [
             {"key": key, "label": label, "type": "text", "required": required}
             for key, label, required in [
                 ("connection_name", "Connection name", True), ("job_name", "Job name", False),
                 ("source_schema", "Source schema", True), ("destination_schema", "Destination schema", True),
                 ("tables", "Table mappings (JSON array)", True),
                 ("description", "Purpose", False),
             ]
         ] + [{"key": "run_now", "label": "Run once now", "type": "boolean", "default": False}]},
    ]


def require_owner(conn_factory, session_id, authorized_owner):
    import calliope as c
    owner = c._owner_for_calliope_session(conn_factory, session_id)
    if owner.strip().lower() != authorized_owner.strip().lower():
        raise PermissionError("The mirror session belongs to another user")
    if not c.calliope_dreams.is_company_admin(conn_factory, owner):
        raise PermissionError("Managing SQL sources requires an Admins Team session")
    return owner


def source_record(conn, name):
    return conn.execute(
        "SELECT c.connection_name,c.label,c.dialect,c.enabled,c.last_test_ok,"
        "EXISTS(SELECT 1 FROM rvbbit.credentials cr WHERE cr.credential_ref=c.credential_ref "
        "AND cr.status='active') AS credential_configured "
        "FROM rvbbit.mirror_connections c WHERE c.connection_name=%s", (name,),
    ).fetchone()


def validate_source(values):
    import calliope as c
    values["connection_name"] = c._setup_mirror_name(values.get("connection_name"))
    values["label"] = c._setup_mirror_text(values.get("label"), "Connection label", 160)
    if values.get("dialect") not in c._SETUP_MIRROR_DIALECTS:
        raise ValueError("Choose a supported SQL source dialect")


def plan(values):
    import calliope as c
    try:
        tables = json.loads(values.get("tables") or "[]")
    except (ValueError, TypeError):
        raise ValueError("Table mappings must be a JSON array") from None
    return c._setup_mirror_plan({**values, "tables": tables, "schedule_seconds": None,
                                 "run_now": bool(values.get("run_now", False))}, values.get("connection_name"))


async def connect_handoff(conn_factory, action, values):
    import calliope as c
    with conn_factory() as conn:
        row = source_record(conn, values["connection_name"])
    if row:
        if row["dialect"] != values["dialect"]:
            raise ValueError("Connection name already belongs to a different database type")
        if not row["enabled"]:
            raise ValueError("Connection is disabled; enable the existing source before using it")
        if row["credential_configured"] and not values.get("replace_credentials") and row.get("last_test_ok") is not False:
            return None  # Execute a normal, durable reuse receipt without credentials.
        if row["credential_configured"]:
            values["replace_credentials"] = True
    return {**c._secure_action_handoff(action, values["connection_name"], ["SOURCE_DSN"]),
            "inputs": values,
            "form_contract": connection_forms.contract(action, values),
            "message": "Use the secure connection card for host/login details, SSH or other connection options. Do not paste credentials into chat. Callie resumes when the test finishes."}


async def execute_connect(conn_factory, run, values, secrets):
    import calliope as c
    name = values["connection_name"]
    run_id = run["id"]
    c._set_action_step(conn_factory, run_id, "apply", "running")
    # Serialize only competing registrations of this name. This is a session
    # advisory lock, NOT a transaction held open while the worker stores a secret.
    with conn_factory() as conn:
        locked = conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked", ("mirror-source:" + name,)).fetchone()
        if not locked["locked"]:
            raise RuntimeError("This source is already being connected; inspect mirror_status when that request finishes")
        try:
            row = source_record(conn, name)
            if row and (row["dialect"] != values["dialect"] or not row["enabled"]):
                raise ValueError("Existing source is disabled or has a different database type")
            reused = bool(row and row["credential_configured"] and not values.get("replace_credentials"))
            if not reused:
                dsn = secrets.get("SOURCE_DSN") or ""
                credential = dsn
                profile = {}
                if secrets.get("CONNECTION_INPUTS"):
                    try:
                        raw = json.loads(secrets["CONNECTION_INPUTS"])
                    except ValueError:
                        raise ValueError("The connection details could not be read. Please submit the secure form again.") from None
                    dialect, profile = connection_forms.sql_profile(raw, values["dialect"])
                    if dialect != values["dialect"]:
                        raise ValueError("The selected database type changed. Ask Callie to open a connection for that type.")
                    dsn = profile["dsn"]
                    credential = json.dumps(profile) if profile.get("ssh") or profile.get("tls") else dsn
                descriptor = c._setup_source_descriptor(dsn, values["dialect"])
                descriptor["transport"] = "ssh" if profile.get("ssh") else "direct"
                descriptor["configured_by"] = run.get("owner_email")
                # Registration is committed before the worker opens its own connection.
                with conn.transaction():
                    conn.execute(
                        "SELECT rvbbit.upsert_mirror_connection(%s,%s,%s,"
                        "coalesce((SELECT metadata FROM rvbbit.mirror_connections WHERE connection_name=%s),'{}'::jsonb)||%s::jsonb,true)",
                        (name, values["label"], values["dialect"], name, json.dumps(descriptor)),
                    )
                stored = await c._setup_mirror_worker_request(
                    conn_factory, "PUT", f"/connections/{quote(name, safe='')}/credential",
                    {"source_dsn": credential}, secret_values=(credential, dsn, *connection_forms.private_values(raw if secrets.get("CONNECTION_INPUTS") else {})),
                )
                if stored.get("ok") is not True:
                    raise RuntimeError("The worker did not confirm credential storage")
                row = source_record(conn, name)
                if not row or not row["credential_configured"]:
                    raise RuntimeError("The source credential was not found in the canonical store")
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", ("mirror-source:" + name,))
    result = {**dict(row), "reused": reused}
    c._set_action_step(conn_factory, run_id, "apply", "complete", result)
    c._set_action_step(conn_factory, run_id, "verify", "running")
    # A failed source probe does not erase a successfully saved credential.
    # Keep the outcome distinct and discoverable; the user can repair connectivity.
    try:
        probe = await c._setup_mirror_worker_request(conn_factory, "POST", f"/connections/{name}/probe")
        verification = {"credential_configured": True, "source_reachable": probe.get("ok") is True,
                        "probe": c._bounded_evidence_json(probe)}
    except Exception as exc:
        verification = {"credential_configured": True, "source_reachable": False,
                        "error": c._action_error_text(exc, secrets.values())}
    c._set_action_step(conn_factory, run_id, "verify", "complete", verification)
    return result, verification


def create_job(conn_factory, run):
    import calliope as c
    import workspace_jobs
    import gallery_jobs
    description = run["input_values"].get("description")
    if description is not None and (not isinstance(description, str) or len(description)>1000):
        raise ValueError("description must be at most 1000 characters")
    workspace_jobs.ensure(conn_factory)
    p = plan(run["input_values"])
    run_id = run["id"]
    c._set_action_step(conn_factory, run_id, "apply", "running")
    with conn_factory() as conn, conn.transaction():
        # A single destination schema belongs to one DLT pipeline. Serialize both
        # name and destination races, including concurrent identical requests.
        for key in sorted(["mirror-job:" + p["job_name"], "mirror-destination:" + p["destination_schema"]]):
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (key,))
        source = source_record(conn, p["connection_name"])
        if not source or not source["enabled"] or not source["credential_configured"]:
            raise ValueError("Connect this SQL source with connect_mirror_source before creating a job")
        existing = conn.execute("SELECT * FROM rvbbit.mirror_jobs WHERE job_name=%s FOR UPDATE", (p["job_name"],)).fetchone()
        queued = None
        if existing:
            fields = ("connection_name", "source_schema", "destination_schema")
            tables = conn.execute("SELECT * FROM rvbbit.mirror_tables WHERE job_name=%s AND enabled ORDER BY source_table",
                                  (p["job_name"],)).fetchall()
            expected = sorted(p["tables"], key=lambda t: t["source_table"])
            actual = [{key: row.get(key) for key in expected[0]} for row in tables]
            if any(existing[key] != p[key] for key in fields) or actual != expected:
                raise ValueError("Job name already exists with different mappings; inspect mirror_status and choose a distinct job/destination or explicitly edit that job")
            # Do not undo a subsequently added schedule/pause or queue a duplicate.
        else:
            if conn.execute("SELECT job_name FROM rvbbit.mirror_jobs WHERE destination_schema=%s LIMIT 1", (p["destination_schema"],)).fetchone():
                raise ValueError("Destination schema already belongs to another mirror job; reuse that job or choose another schema")
            queued = c._apply_setup_mirror_plan(lambda: nullcontext(conn), p, trigger="manual")
        current = conn.execute("SELECT job_name,connection_name,source_schema,destination_schema,enabled,schedule_seconds,next_run_at "
                               "FROM rvbbit.mirror_jobs WHERE job_name=%s", (p["job_name"],)).fetchone()
        gallery_jobs.describe(conn, "mirror", p["job_name"], description or None)
    result = {**c._row_json(current), "reused": bool(existing), "queued_run_id": queued,
              "table_count": len(p["tables"]), "load_complete": False,
              "next_step": "Use mirror_status(job_name=...) for run progress; mirror.manage actions run, pause or schedule this job."}
    c._set_action_step(conn_factory, run_id, "apply", "complete", result)
    c._set_action_step(conn_factory, run_id, "verify", "complete", {"registered": True, "table_count": len(p["tables"])})
    return result, {"registered": True, "queued_run_id": queued, "load_complete": False}


async def discover(conn_factory, connection_name, source_schema="", include_views=False):
    import calliope as c
    name = c._setup_mirror_name(connection_name)
    with conn_factory() as conn:
        row = source_record(conn, name)
    if not row or not row["enabled"] or not row["credential_configured"]:
        raise ValueError("Connect this SQL source with connect_mirror_source first")
    if source_schema:
        schema = c._setup_mirror_text(source_schema, "Source schema")
        return await c._setup_mirror_worker_request(conn_factory, "POST", f"/connections/{name}/discover",
                                                    {"source_schema": schema, "include_views": bool(include_views), "limit": 500})
    return await c._setup_mirror_worker_request(conn_factory, "GET", f"/connections/{name}/schemas")
