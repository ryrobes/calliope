"""Local, fictional transaction desk. One SQL store and A2UI stream for both clients.

No Lab mutations, LLM calls, mail, or external effects. Run with Python 3.11+.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hvac
import counter
import equipment
import receiving
import records
import attachments
import dispatch
import fieldwork
import cases
import json
import os
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from presentation import definition, validate_components, SPEC, VIEWS

ROOT = Path(__file__).resolve().parent
CATALOG = SPEC['id']
SURFACE = "transaction-desk"
STAGES = ("Offer", "Due diligence", "Closing", "Closed")
LOCK = threading.RLock()


class Rejected(ValueError):
    def __init__(self, message, status=400, fields=None):
        super().__init__(message)
        self.status = status
        self.fields = fields or {}


class Store:
    lock = LOCK
    rejection = Rejected
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS deals (
                    id TEXT PRIMARY KEY, address TEXT NOT NULL, neighborhood TEXT NOT NULL,
                    buyer TEXT NOT NULL, price INTEGER NOT NULL, stage TEXT NOT NULL,
                    owner TEXT NOT NULL, closing_date TEXT NOT NULL, notes TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, deal_id TEXT NOT NULL REFERENCES deals(id),
                    title TEXT NOT NULL, due TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY, deal_id TEXT NOT NULL, description TEXT NOT NULL,
                    source TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
            """)
            if not db.execute("SELECT 1 FROM deals LIMIT 1").fetchone():
                today = dt.date.today()
                seeds = [
                    ("cedar", "142 Cedar Lane", "Maplewood · Single-family", "Maya & Daniel Chen", 685000, "Due diligence", "Alex Morgan", 18, "Inspection is back. Roof estimate needed before the contingency deadline."),
                    ("harbor", "8 Harbor View, Unit 4B", "Waterfront · Condominium", "Jordan Ellis", 425000, "Closing", "Sam Rivera", 7, "Cash purchase. Title and final walkthrough are complete; ready for handoff."),
                    ("oak", "306 Oak Street", "Old Town · Townhouse", "Priya Shah", 512000, "Offer", "Alex Morgan", 32, "Buyer prefers a flexible possession date. Confirm the seller's response."),
                    ("willow", "71 Willow Court", "North Hills · Single-family", "Luis & Ana Reyes", 790000, "Due diligence", "Sam Rivera", 25, "Appraisal ordered. Coordinate access with the listing agent."),
                    ("station", "19 Station Road", "Downtown · Loft", "Taylor Brooks", 360000, "Closed", "Alex Morgan", -4, "Keys delivered. Keep the closing packet together for the client."),
                ]
                for ident, address, area, buyer, price, stage, owner, days, notes in seeds:
                    closing = (today + dt.timedelta(days=days)).isoformat()
                    db.execute("INSERT INTO deals VALUES (?,?,?,?,?,?,?,?,?,1)",
                               (ident, address, area, buyer, price, stage, owner, closing, notes))
                    titles = ["Review inspection report", "Confirm financing", "Approve closing packet"]
                    for index, title in enumerate(titles):
                        done = stage in ("Closing", "Closed") or (ident == "cedar" and index == 0)
                        due = (today + dt.timedelta(days=max(1, days - 12 + index * 4))).isoformat()
                        db.execute("INSERT INTO tasks VALUES (?,?,?,?,?)", (f"{ident}-{index}", ident, title, due, int(done)))
                    db.execute("INSERT INTO activity(deal_id,description,source) VALUES (?,?,?)",
                               (ident, "Workspace opened with fictional demo records", "Demo"))
            hvac.seed(db)
            counter.seed(db)
            equipment.seed(db)
            receiving.seed(db)
            dispatch.seed(db)
            fieldwork.seed(db)
            cases.seed(db)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def revision(self, _db=None):
        with (nullcontext(_db) if _db is not None else self.connect()) as db:
            return db.execute("SELECT (SELECT COALESCE(MAX(id),0) FROM activity)+(SELECT COALESCE(MAX(id),0) FROM hvac_activity)+(SELECT COALESCE(MAX(id),0) FROM counter_activity)+(SELECT COALESCE(MAX(id),0) FROM receiving_activity)+(SELECT COALESCE(MAX(id),0) FROM work_case_activity)").fetchone()[0]

    def snapshot(self, selected="cedar", owner="Everyone", notice="", _db=None, view="dossier", state=None):
        if view == "case":
            return cases.snapshot(self, selected, state, notice, _db)
        if view == "adaptive":
            import adaptive
            return adaptive.snapshot(self, selected, notice, _db)
        if view == "dispatch": return dispatch.snapshot(self, selected, state, notice, _db)
        if view == "field": return fieldwork.snapshot(self, selected, notice, _db)
        if view == "receiving":
            try:
                with LOCK: return receiving.snapshot(self, selected, state, notice, _db)
            except ValueError as e: raise Rejected(str(e)) from e
        if view == "counter":
            try:
                with LOCK: return counter.snapshot(self, selected, notice, _db)
            except ValueError as e: raise Rejected(str(e)) from e
        if view == "hvac":
            try:
                with LOCK: return hvac.snapshot(self, selected, state, notice, _db)
            except ValueError as e: raise Rejected(str(e)) from e
        if view not in VIEWS: raise Rejected("Unknown workspace view.")
        if owner not in ("Everyone", "Alex Morgan", "Sam Rivera"):
            raise Rejected("Unknown owner filter.")
        with LOCK, (nullcontext(_db) if _db is not None else self.connect()) as db:
            deals = [dict(x) for x in db.execute("SELECT * FROM deals ORDER BY closing_date,address")]
            deal = next((x for x in deals if x["id"] == selected), None)
            if not deal:
                raise Rejected("This transaction no longer exists.", 404)
            tasks = [dict(x) for x in db.execute("SELECT * FROM tasks WHERE deal_id=? ORDER BY due,id", (selected,))]
            all_tasks = [dict(x) for x in db.execute("SELECT * FROM tasks ORDER BY due,id")]
            activity = [dict(x) for x in db.execute("SELECT * FROM activity WHERE deal_id=? ORDER BY id DESC LIMIT 5", (selected,))]
            revision = self.revision(db)
        visible = [x for x in deals if owner == "Everyone" or x["owner"] == owner]
        open_deals = [x for x in deals if x["stage"] != "Closed"]
        left = sum(not x["done"] for x in tasks)
        data = {
            "draft": {k: deal[k] for k in ("id", "version", "owner", "closing_date", "notes")},
            "filter": {"owner": [owner]},
            "summary": {"active": str(len(open_deals)), "volume": f"${sum(x['price'] for x in open_deals):,}",
                        "closing": str(sum(x["stage"] == "Closing" for x in deals))},
            "selected": {**deal, "price_label": f"${deal['price']:,}", "readiness": "Ready for the next step" if not left else f"{left} open commitments"},
        }
        components = definition(visible, deal, tasks, activity, all_tasks, view)
        validate_components(components)
        messages = [
            {"version": "v0.9", "createSurface": {"surfaceId": SURFACE, "catalogId": CATALOG}},
            {"version": "v0.9", "updateComponents": {"surfaceId": SURFACE, "components": components}},
            {"version": "v0.9", "updateDataModel": {"surfaceId": SURFACE, "path": "/", "value": data}},
        ]
        return {"messages": messages, "selected": selected, "owner": owner, "revision": revision,
                "draft": data["draft"], "view": view, "notice": notice, "application": "calliope-transaction-desk"}

    def action(self, body):
        panes=body.get('panes',[])
        name=body.get('name')
        if name in ('open_record','close_record','back_record'):
            if not isinstance(panes,list):raise Rejected('Invalid record navigation.')
            if name=='open_record':
                ctx=body.get('context',{})
                if not isinstance(ctx,dict):raise Rejected('Invalid record reference.')
                panes=[*panes[-7:],{'kind':ctx.get('kind'),'id':ctx.get('recordId')}]
            else:panes=panes[:-1] if name=='back_record' else []
            body={**body,'name':'refresh','context':{}}
        elif name in ('set_view','select_deal','hvac_select','counter_select','receiving_select','adaptive_select','case_select'):
            panes=[]
        records.validate_refs(self,panes)
        return records.decorate(self,self._action(body),panes)

    def _action(self, body):
        name, context = body.get("name"), body.get("context", {})
        if not isinstance(context, dict):
            raise Rejected("Action context must be an object.")
        selected = str(context.get("id") or body.get("selected") or "cedar")
        owner = body.get("owner", "Everyone")
        view = context.get("view", body.get("view", "dossier"))
        if view not in VIEWS: raise Rejected("Unknown workspace view.")
        if view == "case": return cases.action(self, body, name, context, selected)
        if view == "adaptive":
            if name not in ("adaptive_select","refresh","set_view","discard"): raise Rejected("Unknown adaptive action")
            return self.snapshot(selected, view=view)
        if view == "dispatch": return dispatch.action(self, body, name, context, selected)
        if view == "field": return fieldwork.action(self, body, name, context, selected)
        if view == "hvac":
            return self.hvac_action(body, name, context, selected)
        if view == "receiving": return receiving.action(self, body, name, context, selected)
        if view == "counter": return counter.action(self, body, name, context, selected)
        if name == "set_view" and body.get("view") in ("hvac", "counter", "receiving", "dispatch", "field", "adaptive", "case") and not context.get("id"): selected = "cedar"
        if name in ("select_deal", "refresh", "discard", "filter", "set_view"):
            if name == "filter":
                values = context.get("owner", ["Everyone"])
                owner = values[0] if isinstance(values, list) and values else "Everyone"
            return self.snapshot(selected, owner, view=view)
        if name not in ("save_deal", "complete_task", "advance_deal"):
            raise Rejected("Unknown transaction action.")
        request_id = body.get("requestId")
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 100:
            raise Rejected("A request ID is required.")
        fingerprint = hashlib.sha256(json.dumps([name, context], sort_keys=True).encode()).hexdigest()
        with LOCK, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise Rejected("This request ID was already used for a different change.", 409)
                return json.loads(previous["result"])
            row = db.execute("SELECT * FROM deals WHERE id=?", (selected,)).fetchone()
            if not row:
                raise Rejected("Transaction not found.", 404)
            if type(context.get("version")) is not int or context["version"] != row["version"]:
                raise Rejected("This transaction changed in another view. Your draft is kept. Copy any changes you need, then Discard / reload to reconcile.", 409)
            if name == "save_deal":
                notes, assignee, closing = context.get("notes"), context.get("owner"), context.get("closing_date")
                if not isinstance(notes, str) or len(notes) > 4000:
                    raise Rejected("Notes must be at most 4,000 characters.")
                if assignee not in ("Alex Morgan", "Sam Rivera"):
                    raise Rejected("Owner must be Alex Morgan or Sam Rivera.")
                try:
                    if not isinstance(closing, str) or dt.date.fromisoformat(closing).isoformat() != closing:
                        raise ValueError()
                except ValueError:
                    raise Rejected("Use a real closing date in YYYY-MM-DD format.") from None
                db.execute("UPDATE deals SET owner=?,closing_date=?,notes=?,version=version+1 WHERE id=?",
                           (assignee, closing, notes, selected))
                description = "Updated owner, closing date and working notes"
            elif name == "complete_task":
                task = db.execute("SELECT * FROM tasks WHERE id=? AND deal_id=?", (context.get("task_id"), selected)).fetchone()
                if not task or task["done"]:
                    raise Rejected("This commitment is no longer open. Reload the transaction.", 409)
                db.execute("UPDATE tasks SET done=1 WHERE id=?", (task["id"],))
                db.execute("UPDATE deals SET version=version+1 WHERE id=?", (selected,))
                description = f"Completed: {task['title']}"
            else:
                index = STAGES.index(row["stage"])
                if index == len(STAGES)-1:
                    raise Rejected("This transaction is already closed.")
                if db.execute("SELECT 1 FROM tasks WHERE deal_id=? AND done=0", (selected,)).fetchone():
                    raise Rejected("Complete the open commitments before advancing the transaction.")
                stage = STAGES[index+1]
                db.execute("UPDATE deals SET stage=?,version=version+1 WHERE id=?", (stage, selected))
                description = f"Advanced transaction to {stage}"
            source = "Desktop" if body.get("source") == "Desktop" else "Web"
            db.execute("INSERT INTO activity(deal_id,description,source) VALUES (?,?,?)", (selected, description, source))
            # The change and replay response commit in the same transaction.
            result = self.snapshot(selected, owner, description, _db=db, view=view)
            db.execute("INSERT INTO requests VALUES (?,?,?)", (request_id, fingerprint, json.dumps(result)))
            return result

    def hvac_action(self, body, name, context, selected):
        try:
            state = hvac.filters(body.get('state'))
            if name == 'hvac_filter':
                techs, days = context.get('technician'), context.get('days')
                if not (isinstance(techs,list) and len(techs)==1 and isinstance(days,list) and len(days)==1):
                    raise ValueError('Choose one technician and one reporting period.')
                if days[0] not in ('7','28','56'):
                    raise ValueError('Choose a reporting period: 7, 28, or 56 days.')
                state = hvac.filters({'technician':techs[0],'days':int(days[0])})
            if name == 'hvac_chart_select':
                with self.connect() as db: state=hvac.select_chart(db,state,context)
                selected=None
            if name == 'hvac_chart_clear':
                state.pop('chart_selection',None)
                selected=None
        except (ValueError, TypeError) as e: raise Rejected(str(e)) from e
        if name in ('set_view','refresh','discard','hvac_filter','hvac_select','hvac_chart_select','hvac_chart_clear'):
            return self.snapshot(selected,view='hvac',state=state)
        if name not in ('save_deal','hvac_advance'): raise Rejected('Unknown HVAC action.')
        request_id = body.get('requestId')
        if not isinstance(request_id,str) or not 8<=len(request_id)<=100: raise Rejected('A request ID is required.')
        fingerprint=hashlib.sha256(json.dumps(['hvac',name,context],sort_keys=True).encode()).hexdigest()
        with LOCK, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM requests WHERE id=?',(request_id,)).fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint: raise Rejected('Request ID already used for another change.',409)
                return json.loads(previous['result'])
            row=db.execute('SELECT * FROM hvac_jobs WHERE id=?',(selected,)).fetchone()
            if not row: raise Rejected('Visit not found.',404)
            if type(context.get('version')) is not int or context['version']!=row['version']:
                raise Rejected('This visit changed in another view. Your draft is kept; discard / reload to reconcile.',409)
            if name=='save_deal':
                notes=context.get('notes')
                if not isinstance(notes,str) or len(notes)>4000: raise Rejected('Notes must be at most 4,000 characters.')
                db.execute('UPDATE hvac_jobs SET notes=?,version=version+1 WHERE id=?',(notes,selected))
                description='Updated visit notes'
            else:
                index=hvac.STATUSES.index(row['status'])
                if index==len(hvac.STATUSES)-1: raise Rejected('Visit is already complete.')
                status=hvac.STATUSES[index+1]
                db.execute('UPDATE hvac_jobs SET status=?,version=version+1 WHERE id=?',(status,selected))
                description='Visit moved to '+status
            source='Desktop' if body.get('source')=='Desktop' else 'Web'
            db.execute('INSERT INTO hvac_activity(job_id,description,source) VALUES (?,?,?)',(selected,description,source))
            result=self.snapshot(selected,notice=description,_db=db,view='hvac',state=state)
            db.execute('INSERT INTO requests VALUES (?,?,?)',(request_id,fingerprint,json.dumps(result)))
            return result




class Handler(BaseHTTPRequestHandler):
    server_version = "CalliopeBusiness/1"

    def reply(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in allowed:
            raise Rejected("Invalid local host.", 403)
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://"+x for x in allowed}:
            raise Rejected("Cross-origin requests are not allowed.", 403)

    def do_GET(self):
        try:
            self.trusted()
            parsed = urlparse(self.path)
            args = parse_qs(parsed.query)
            if parsed.path == "/api/health":
                return self.reply(200, {"application": "calliope-transaction-desk", "revision": self.server.store.revision()})
            if parsed.path == "/api/cases":
                return self.reply(200, {"cases": cases.summaries(self.server.store)})
            if parsed.path == "/api/workspace":
                return self.reply(200, self.server.store.snapshot(args.get("deal", ["cedar"])[0], args.get("owner", ["Everyone"])[0], view=args.get("view",["dossier"])[0], state={"technician":args.get("technician",["Everyone"])[0],"days":int(args.get("days",["28"])[0])}))
            if parsed.path == "/api/definition":
                return self.reply(200, self.server.store.snapshot(view=args.get("view",["dossier"])[0])["messages"])
            if parsed.path == "/api/catalog":
                return self.reply(200, SPEC)
            if parsed.path == "/api/theme":
                path = self.server.store.path.parent / "business-theme.json"
                return self.reply(200, json.loads(path.read_text()) if path.exists() else {})
            if parsed.path.startswith('/api/attachments/'):
                attachment=attachments.get(self.server.store,parsed.path.rsplit('/',1)[-1])
                self.send_response(200)
                self.send_header('Content-Type',attachment['mime'])
                self.send_header('Content-Length',str(attachment['size']))
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Content-Security-Policy',"sandbox; default-src 'none'")
                from urllib.parse import quote
                self.send_header('Content-Disposition',"inline; filename*=UTF-8''"+quote(attachment['name']))
                self.send_header('Cache-Control','no-store')
                self.end_headers();self.wfile.write(attachment['content']);return
            # Only known theme fonts and the web bundle are exposed.
            if parsed.path in ("/fonts/LTSuperior-Regular.otf", "/fonts/LTSuperiorSerif-Regular.otf", "/fonts/LTSuperiorMono-Regular.otf"):
                root = (self.server.store.path.parent / "fonts").resolve()
                path = next(root.glob("*/"+Path(parsed.path).name), root/"missing")
            elif parsed.path == "/art/neighborhood.png":
                root = (ROOT/"assets").resolve()
                path = root/"neighborhood.png"
            else:
                relative = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
                root = (ROOT/"web/dist").resolve()
                path = (root/relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return self.reply(404, {"error": "Build the web client with npm run build."})
            import mimetypes
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except Rejected as exc:
            self.reply(exc.status, {"error": str(exc), "fields": exc.fields})

        except (ValueError, TypeError):
            self.reply(400, {"error": "Invalid query."})

    def do_POST(self):
        try:
            self.trusted()
            endpoint=urlparse(self.path).path
            if endpoint not in ("/api/action","/api/attachments","/api/field-reports","/api/reactive"):
                raise Rejected("Unknown endpoint.", 404)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise Rejected("JSON is required.", 415)
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= (9*1024*1024 if endpoint in ("/api/attachments","/api/field-reports") else 65536):
                raise Rejected("Invalid request size.", 413)
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise Rejected("Expected an action object.")
            if endpoint == '/api/reactive':
                import reactive
                try: return self.reply(200, reactive.evaluate(body))
                except Exception: return self.reply(503, {'error':'Decision unavailable. The previous view and your edits are kept.'})
            if endpoint=="/api/field-reports": return self.reply(200,fieldwork.submit(self.server.store,body))
            self.reply(200, attachments.upload(self.server.store,body) if endpoint=="/api/attachments" else self.server.store.action(body))
        except Rejected as exc:
            self.reply(exc.status, {"error": str(exc), "fields": exc.fields})
        except (ValueError, TypeError):
            self.reply(400, {"error": "Invalid request."})


def main():
    # The canvas launcher owns fd 9's singleton flock. The long-lived service
    # must not retain that lock after the desktop closes.
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", Path(os.environ.get("XDG_CACHE_HOME", Path.home()/".cache"))/"calliope-canvas"))
    try:
        if os.path.samestat(os.fstat(9), (runtime/"calliope-canvas.lock").stat()):
            os.close(9)
    except OSError:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18455)
    parser.add_argument("--db", default=str(Path(os.environ.get("XDG_DATA_HOME", Path.home()/".local/share"))/"calliope-canvas/business-demo.sqlite"))
    args = parser.parse_args()
    os.umask(0o077)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.store = Store(args.db)
    print(f"Transaction desk: http://127.0.0.1:{args.port} · fictional local data", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
