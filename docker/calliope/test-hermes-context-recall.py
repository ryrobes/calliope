"""Native-path regressions: lost evidence, scoped recall, async indexing and MCP deadlines.

Runs in the pinned image without external inference. Live service probes are separate.
"""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent.context_compressor import ContextCompressor, _is_summary_stub
from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, InlineToolContext, resolve_invoke_tool_executor
from agent.calliope_tool_deadlines import tool_deadline
from tools import calliope_middle_memory as middle
from tools import calliope_session_context as recall
from tools import calliope_compaction_scope as scope

SQL = "mcp__Warehouse__run_workspace_sql"
CONFIG = {"mcp_servers": {"Warehouse": {"timeout": 90, "tool_timeouts": {"run_workspace_sql": 660}}}}


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SimpleNamespace(db_path=Path(self.tmp.name)/"state.db", _lock=threading.RLock())
        self.db._conn = sqlite3.connect(self.db.db_path, check_same_thread=False)
        self.db._conn.row_factory = sqlite3.Row
        self.db._conn.execute("""CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
            content TEXT, tool_calls TEXT, tool_call_id TEXT, tool_name TEXT, timestamp REAL DEFAULT 0,
            active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0, _compressed_summary INTEGER DEFAULT 0)""")
        self.settings = patch.object(middle, "config", return_value={"enabled": False})
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        self.db._conn.close()
        self.tmp.cleanup()

    def add(self, content, session="mine", role="tool", call="q1", tool=SQL, active=1, compacted=0, calls=None):
        cur = self.db._conn.execute("INSERT INTO messages(session_id,role,content,tool_calls,tool_call_id,tool_name,active,compacted) VALUES (?,?,?,?,?,?,?,?)",
                                    (session,role,content,calls,call,tool,active,compacted))
        self.db._conn.commit()
        return cur.lastrowid

    def read(self, **args):
        return json.loads(recall.session_context(args, db=self.db, session_id="mine"))

    def test_original_survives_two_compactions_and_exact_lookup(self):
        body = json.dumps({"row_count": 5035, "rows": [{"state": "Washington", "n": 616}], "notes": "x"*18000})
        self.add(body, active=0, compacted=1)
        messages = [{"role": "tool", "content": body, "tool_call_id": "q1"}]
        mapping = {"q1": (SQL, '{"sql":"SELECT state,count(*) FROM reports GROUP BY state"}')}
        self.assertTrue(ContextCompressor._demote_tool_result_at(messages, 0, mapping, 200))
        digest = messages[0]["content"]
        self.assertIn('"n":616', digest)
        self.assertIn("5035", digest)
        self.assertIn('"tool_call_id":"q1"', digest)
        self.assertLessEqual(len(digest), recall.MAX_DIGEST)
        self.assertTrue(_is_summary_stub(digest))
        self.assertFalse(ContextCompressor._demote_tool_result_at(messages, 0, mapping, 200))
        self.add(digest)
        self.assertEqual(self.read(tool_call_id="q1")["content"], body[:6000])
        self.assertEqual(self.read(tool_call_id="q1", pointer="/rows/0/n")["content"], "616")

    def test_scope_cannot_be_overridden_and_undo_stays_hidden(self):
        other = self.add("secret from another session", session="other", call="other")
        self.add("rewound work", call="undo", active=0, compacted=0)
        self.assertIn("error", self.read(message_id=other))
        self.assertIn("error", self.read(tool_call_id="other"))
        self.assertIn("error", self.read(session_id="other"))
        self.assertIn("error", self.read(tool_call_id="undo"))
        self.assertEqual(self.read(query="secret")["matches"], [])

    def test_search_past_native_fts_prefix_and_page_exact_original(self):
        body = "x"*10000 + "\nrare_shape_mapping bright_disk=29\n" + "y"*20000
        mid = self.add(body, active=0, compacted=1)
        self.add(body)  # compaction copy
        result = self.read(query="rare_shape_mapping")
        self.assertEqual(len(result["matches"]), 1)
        self.assertIn("bright_disk=29", result["matches"][0]["excerpt"])
        pages, offset = [], 0
        while offset is not None:
            page = self.read(message_id=mid, offset=offset, limit=1000)
            pages.append(page["content"]); offset = page["next_offset"]
        self.assertEqual("".join(pages), body)

    def test_arguments_and_user_messages_searchable(self):
        self.add("Please keep all the rows", role="user", call=None, tool=None)
        self.add("", role="assistant", call=None, tool=None, calls='[{"function":{"name":"run_sql","arguments":"SELECT atmospheric_glow"}}]')
        self.assertEqual(self.read(query="atmospheric_glow")["matches"][0]["role"], "assistant")
        self.assertEqual(self.read(query="keep rows")["matches"][0]["role"], "user")

    def test_sequential_and_concurrent_inline_bind_real_agent(self):
        self.add("bound result")
        agent = SimpleNamespace(session_id="mine", _get_session_db_for_recall=lambda: self.db)
        ctx = InlineToolContext(effective_task_id="untrusted-display-task")
        for executor in (INLINE_TOOL_EXECUTORS["session_context"], resolve_invoke_tool_executor(agent, "session_context")):
            result = json.loads(executor(agent, {"tool_call_id": "q1"}, ctx))
            self.assertEqual(result["content"], "bound result")
            self.assertEqual(result["session_id"], "mine")

    def test_lean_pressure_path_retains_values_and_recovery(self):
        compressor = object.__new__(ContextCompressor)
        compressor.quiet_mode = True
        content = json.dumps({"rows": [{"count": 80328}], "detail": "z"*10000})
        messages = []
        for i in range(10):
            messages += [{"role": "assistant", "content": "check"},
                         {"role": "tool", "content": content, "tool_name": SQL, "tool_call_id": str(i)}]
        compacted = compressor._demote_stale_tail_tools(messages, 0)
        self.assertIn("80328", compacted[1]["content"])
        self.assertIn("session_context", compacted[1]["content"])
        self.assertEqual(messages[1]["content"], content)
        self.assertEqual(compacted[-1]["content"], content)
        summary = compressor._augment_summary_lean("summary", [])
        self.assertIn("Session evidence recovery", summary)
        self.assertEqual(recall.recovery_footer(summary), summary)

    def test_small_results_and_errors_remain_useful(self):
        content = '{"status":"failed","error":"statement timeout","run":{"status":"failed"}}'
        digest = recall.retain_tool_evidence(SQL, "{}", content, "failed-1")
        self.assertIn("statement timeout", digest)
        self.assertIn("failed-1", digest)
        messages = [{"role":"tool","content":content,"tool_call_id":"failed-1"}]
        self.assertFalse(ContextCompressor._demote_tool_result_at(messages,0,{},1))

    def test_batch_digest_preserves_every_result_and_late_failure(self):
        results = {f"audit_{i}": {"row_count":1,"rows":[{"count":5035+i}],"truncated":False,
                                  "rvbbit_tables":[{"storage_metadata":"noise"*10000}]} for i in range(7)}
        results['late/~failure'] = {"error":{"code":"SQL_ERROR","message":"function missing"}}
        body = json.dumps({"results":results,"result_mode":"full"})
        wrapped = '<untrusted_tool_result source="sql">External data\n\n' + json.dumps({"result":body}) + '</untrusted_tool_result>'
        self.add(wrapped, call="batch", active=0, compacted=1)
        messages = [{"role":"tool","content":wrapped,"tool_call_id":"batch"}]
        self.assertTrue(ContextCompressor._demote_tool_result_at(messages,0,{"batch":("run_sql_multi","{}")},200))
        digest = messages[0]["content"]
        inventory = json.loads(digest.split('\n',1)[1].split('\nExact original:')[0])
        self.assertEqual(inventory['query_count'],8)
        self.assertEqual(len(inventory['queries']),8)
        self.assertNotIn('storage_metadata',digest)
        self.assertLessEqual(len(digest),recall.MAX_BATCH_DIGEST)
        for i,entry in enumerate(inventory['queries'][:-1]):
            self.assertEqual(entry['pointer'],f'/results/audit_{i}')
            self.assertEqual(entry['row_count'],1)
            self.assertIn(str(5035+i),json.dumps(entry['preview']))
        failure = inventory['queries'][-1]
        self.assertEqual(failure['status'],'error')
        self.assertIn('function missing',failure['error'])
        original = json.loads(self.read(tool_call_id='batch',pointer=failure['pointer'])['content'])
        self.assertEqual(original,results['late/~failure'])
        self.assertEqual(recall.retain_tool_evidence('run_sql_multi','{}',digest,'batch'),digest)
        self.assertEqual(messages[0]['tool_call_id'],'batch')

    def test_large_batch_digest_keeps_inventory_bounded_and_marks_overflow(self):
        # Max-size batches, long identifiers, huge cells, late errors, summary mode.
        results = {f'query_{i}_'+'z'*120:{'row_count':5000,'truncated':True,
                   'preview':[{'description':'\\"'*10000}], 'password':'digest-test-secret'} for i in range(24)}
        last = list(results)[-1]
        results[last] = {'error':{'code':'SQL_ERROR','message':'last member failed','hint':'password=digest-test-secret'}}
        digest = recall.retain_tool_evidence('run_sql_multi','{}',json.dumps({'results':results,'result_mode':'summary'}),'batch')
        record = json.loads(digest.split('\n',1)[1].split('\nExact original:')[0])
        self.assertLessEqual(len(digest),recall.MAX_BATCH_DIGEST)
        self.assertEqual([e['pointer'] for e in record['queries']],['/results/'+name for name in results])
        self.assertIn('last member failed',digest)
        self.assertNotIn('digest-test-secret',digest)
        self.assertTrue(all(e['truncated'] for e in record['queries'][:-1]))
        pathological = {'q'*20000:{'rows':[],'row_count':0}}
        digest = recall.retain_tool_evidence('run_sql_multi','{}',json.dumps({'results':pathological}),'batch')
        self.assertLessEqual(len(digest),recall.MAX_BATCH_DIGEST)
        self.assertIn('"inventory_partial":true',digest)
        self.assertIn('"query_count":1',digest)
        self.assertIn('"pointer":"/results"',digest)

    def test_compaction_prompt_sees_retained_task_and_later_receipts(self):
        compressor = ContextCompressor(model="test", config_context_length=200000, quiet_mode=True)
        request = "Build the dashboard. " + "More detailed user requirements. " * 25 + "Use Mapbox. Do not deploy."
        messages = [
            {"role": "user", "content": request},
            {"role": "assistant", "content": "Inspecting schema"},
            {"role": "tool", "content": "schema", "tool_name": "describe_table", "tool_call_id": "schema"},
            {"role": "tool", "content": "{\"rows\":[{\"n\":5035}]}", "tool_name": "run_sql_multi", "tool_call_id": "query-done"},
        ]
        compressor._calliope_summary_scope = scope.capture_scope(messages, 1, 3)
        prompt = compressor._build_summary_prompt("schema only", 1000, "Build the dashboard", "", True)
        self.assertIn(request, prompt)
        self.assertIn("query-done", prompt)
        self.assertIn("run_sql_multi", prompt)
        self.assertIn("not observed in this segment", prompt)
        self.assertIn("Do not invent implementation choices", prompt)
        # Iterative checkpoints carry the same boundary, not an unqualified global-state request.
        compressor._previous_summary = "Old discovery summary"
        prompt = compressor._build_summary_prompt("schema only", 1000, None, "", True)
        self.assertIn("state at the end of the compacted segment", prompt)
        self.assertNotIn('Update "Active State" to reflect current state.', prompt)

    def test_scope_keeps_stop_corrections_bounded_redacted_and_excludes_notices(self):
        messages = [{"role":"user", "content":"Original request. " + "x"*10000 + " End constraint."},
                    {"role":"user", "content":"Stop building; just inspect. password=scope-test-secret"},
                    {"role":"user", "content":"internal-noise", "display_kind":"notification"},
                    {"role":"user", "content":ContextCompressor._with_summary_prefix("stale-task")}]
        original = copy.deepcopy(messages)
        captured = scope.capture_scope(messages, 1, 2)
        rendered = scope.scope_prompt(captured)
        self.assertIn("Stop building; just inspect", rendered)
        self.assertIn("End constraint", rendered)
        self.assertNotIn("scope-test-secret", rendered)
        self.assertNotIn("internal-noise", rendered)
        self.assertNotIn("stale-task", rendered)
        self.assertTrue(captured["recent_user_requests"][0]["partial"])
        self.assertLess(len(rendered), 22000)
        self.assertEqual(messages, original)

    def test_generated_and_fallback_checkpoints_have_one_scope_label_and_reset(self):
        compressor = ContextCompressor(model="test", config_context_length=200000, quiet_mode=True)
        compressor._calliope_summary_scope = {"segment_messages":4,"later_messages":18,
                                             "later_tool_result_count":5,"later_tool_counts":{"run_sql_multi":5}}
        generated = compressor._augment_summary_lean("## Active State\nSchema examined.", [])
        self.assertIn("4 earlier messages", generated)
        self.assertIn("18 later messages", generated)
        self.assertIn("run_sql_multi: 5", generated)
        self.assertTrue(ContextCompressor._is_context_summary_content(generated))
        repeated = compressor._augment_summary_lean(generated, [])
        self.assertEqual(repeated.count(scope.SCOPE_HEADING), 1)
        self.assertIn("Schema examined.", repeated)
        fallback = compressor._build_static_fallback_summary([])
        self.assertIn(scope.SCOPE_HEADING, fallback)
        self.assertIn("18 later messages", fallback)
        compressor.on_session_reset()
        self.assertIsNone(compressor._calliope_summary_scope)

    def test_native_compress_supplies_scope_without_replacing_active_request(self):
        compressor = ContextCompressor(model="test", config_context_length=200000, quiet_mode=True,
                                       protect_first_n=1, protect_last_n=2)
        request = "Use Mapbox, keep all rows, and do not deploy."
        messages = [{"role":"user","content":request}]
        for i in range(5):
            messages.extend([
                {"role":"assistant", "content":"", "tool_calls":[{"id":str(i),"type":"function",
                 "function":{"name":"run_sql_multi","arguments":"{}"}}]},
                {"role":"tool","tool_call_id":str(i),"tool_name":"run_sql_multi","content":'{"rows":[{"n":5035}]}'}])
        with patch.object(compressor,"_compress_window",return_value=(1,3)), \
             patch.object(compressor,"_call_summary_llm",return_value="## Completed Actions\nFirst batch returned 5035.") as llm:
            output = compressor.compress(messages,current_tokens=150000,force=True)
        prompt = llm.call_args.args[0]
        self.assertIn(request,prompt)
        self.assertIn('"later_tool_result_count": 4',prompt)
        summaries = [m for m in output if m.get('_compressed_summary')]
        self.assertEqual(len(summaries),1)
        self.assertIn("2 earlier messages",summaries[0]['content'])
        self.assertTrue(any(m.get('role')=='user' and request in (m.get('content') or '') for m in output))

    def test_index_chunks_all_text_once_and_scopes_semantic_recall(self):
        mine = self.add("luminous vehicles\n" + "a"*11000, active=0, compacted=1)
        self.add("luminous vehicles\n" + "a"*11000)
        other = self.add("luminous vehicles hidden", session="other", call="other")
        self.add("do not index undo", active=0, compacted=0, call="undo")
        middle.ingest(self.db, "mine")
        middle.ingest(self.db, "other")
        with middle.index(self.db) as cache:
            chunks = [dict(r) for r in cache.execute("SELECT * FROM chunks WHERE session_id='mine'")]
            self.assertGreaterEqual(len(chunks), 5)
            self.assertTrue(all(r["message_id"] == mine for r in chunks))
            self.assertGreater(max(r["chunk_offset"] for r in chunks), 8192)
            cache.execute("UPDATE chunks SET vector='[1,0]',model_version='m:v1'")
        with patch.object(middle,"config",return_value={"judge":True}), patch.object(middle,"queue_index"), \
             patch.object(middle,"embed",return_value=([[1,0]],"m:v1")), \
             patch.object(middle,"_request",return_value=({"outputs":[{"answers":{"0":{"probabilities":{"relevant":.98}}}}]},"jev")) as judge:
            found,status = middle.semantic_candidates(self.db,"mine","unidentified flying objects")
            self.assertEqual(found[0]["message_id"],mine)
            self.assertNotIn(other,[r["message_id"] for r in found])
            self.assertEqual(status["judge"],"jev")
            self.assertIn("luminous vehicles",json.dumps(judge.call_args))
            result = self.read(query="unidentified flying objects")
            self.assertEqual(result["search_method"],"hybrid")
            self.assertEqual(result["matches"][0]["message_id"],mine)

    def test_embedding_failure_falls_back_to_exact_text(self):
        self.add("known mapping")
        with patch.object(middle,"semantic_candidates",side_effect=TimeoutError):
            result = self.read(query="mapping")
        self.assertEqual(result["matches"][0]["tool_call_id"],"q1")
        self.assertEqual(result["retrieval"]["semantic"],"unavailable")

    def test_semantic_recall_rebinds_superseded_copies_without_reembedding(self):
        source = self.add('luminous vehicles',call='evidence')
        user = self.add('Use the municipal time zone',role='user',call=None,tool=None)
        middle.ingest(self.db,'mine')
        with middle.index(self.db) as cache:
            cache.execute("UPDATE chunks SET vector='[1,0]',model_version='m:v1'")
            count = cache.execute('SELECT count(*) FROM chunks').fetchone()[0]
        self.db._conn.execute('UPDATE messages SET active=0 WHERE id IN (?,?)',(source,user))
        first_copy = self.add('luminous vehicles',call='evidence')
        user_copy = self.add('Use the municipal time zone',role='user',call=None,tool=None)
        self.add('Use the municipal time zone',role='user',call=None,tool=None)
        middle.ingest(self.db,'mine')
        with middle.index(self.db) as cache:
            self.assertEqual(cache.execute('SELECT count(*) FROM chunks').fetchone()[0],count)
            self.assertEqual(cache.execute('SELECT count(*) FROM chunks WHERE vector IS NOT NULL').fetchone()[0],count)
        # Another compaction can happen before the asynchronous index catches up.
        self.db._conn.execute('UPDATE messages SET active=0 WHERE id=?',(first_copy,))
        newest = self.add('luminous vehicles',call='evidence',active=0,compacted=1)
        with patch.object(middle,'config',return_value={'enabled':True,'judge':False}),patch.object(middle,'queue_index'), \
             patch.object(middle,'embed',return_value=([[1,0]],'m:v1')):
            result = self.read(query='unidentified objects')
            self.assertEqual({m['message_id'] for m in result['matches']},{newest,user_copy})
            self.assertEqual(self.read(message_id=newest)['content'],'luminous vehicles')
            # Exact and semantic hits for the same call appear once.
            matches = self.read(query='luminous')['matches']
            self.assertEqual(sum(m['tool_call_id']=='evidence' for m in matches),1)
            self.assertEqual(sum(m['role']=='user' for m in self.read(query='municipal')['matches']),1)
        # Serving independently rechecks visibility, even with an older shortlist.
        with patch.object(middle,'semantic_candidates',return_value=([{'message_id':source,'excerpt':'luminous vehicles'}],{'semantic':'ready'})):
            self.assertEqual(self.read(query='unknownphrase')['matches'][0]['message_id'],newest)

    def test_semantic_shortlist_excludes_undo_deleted_cross_scope_and_summaries(self):
        hidden = [self.add(f'hidden {i}',call=f'undo{i}') for i in range(9)]
        deleted = self.add('deleted evidence',call='deleted')
        good = self.add('luminous vehicles',call='good')
        middle.ingest(self.db,'mine')
        with middle.index(self.db) as cache:
            cache.execute("UPDATE chunks SET vector='[1,0]',model_version='m:v1'")
        self.db._conn.execute('UPDATE messages SET active=0 WHERE id<=?',(hidden[-1],))
        self.db._conn.execute('DELETE FROM messages WHERE id=?',(deleted,))
        # Similar-looking copies do not authorize a different scope, tool, or summary.
        other = self.add('hidden 0',call='undo0',session='other')
        self.add('hidden 1',call='undo1',tool='different_tool')
        summary = self.add('hidden 2',call='undo2')
        self.db._conn.execute('UPDATE messages SET _compressed_summary=1 WHERE id=?',(summary,))
        self.db._conn.commit()
        with patch.object(middle,'config',return_value={'enabled':True,'judge':True}),patch.object(middle,'queue_index'), \
             patch.object(middle,'embed',return_value=([[1,0]],'m:v1')), \
             patch.object(middle,'_request',return_value=({'outputs':[{'answers':{'0':{'probabilities':{'relevant':.9}}}}]},'jev')) as judge:
            found,_ = middle.semantic_candidates(self.db,'mine','unidentified objects')
            self.assertEqual([m['message_id'] for m in found],[good])
            self.assertNotIn('hidden',json.dumps(judge.call_args))
            self.assertNotIn('deleted evidence',json.dumps(judge.call_args))
        with patch.object(middle,'semantic_candidates',return_value=([{'message_id':mid} for mid in [*hidden,deleted,other,summary,good]],{'semantic':'ready'})):
            self.assertEqual(self.read(query='unknownphrase',role='user')['matches'],[])
            self.assertEqual(self.read(query='unknownphrase',tool_name='unrelated')['matches'],[])
            self.assertEqual([m['message_id'] for m in self.read(query='unknownphrase')['matches']],[good])

    def test_corrupt_index_cannot_break_native_recall(self):
        self.add('known original mapping')
        (Path(self.db.db_path).parent/'session-context-v1.sqlite').write_bytes(b'broken index')
        self.assertEqual(self.read(tool_call_id='q1')['content'],'known original mapping')
        self.assertEqual(self.read(query='mapping')['matches'][0]['tool_call_id'],'q1')

    def test_background_jobs_do_not_inherit_a_foreground_http_client(self):
        submitted=[]
        pool=SimpleNamespace(submit=lambda fn:submitted.append(fn))
        parent=object()
        token=middle._http_client.set(parent)
        try:
            with patch.object(middle,'config',return_value={'enabled':True}),patch.object(middle,'_pool',pool):
                middle.queue_index(self.db,'mine')
                self.assertEqual(len(submitted),1)
                with patch.object(middle,'_worker',side_effect=lambda *a:self.assertIsNone(middle._http_client.get())):
                    submitted[0]()
                self.assertIs(middle._http_client.get(),parent)
        finally:
            middle._http_client.reset(token)
            middle._workers.discard((str(Path(self.db.db_path).parent),'mine'))

    def test_jev_failure_keeps_dense_candidates_and_encoder_change_reindexes(self):
        self.add('luminous vehicles')
        middle.ingest(self.db,'mine')
        with middle.index(self.db) as cache:cache.execute("UPDATE chunks SET vector='[1,0]',model_version='m:v1'")
        with patch.object(middle,'config',return_value={'enabled':True,'judge':True}),patch.object(middle,'queue_index'), \
             patch.object(middle,'embed',return_value=([[1,0]],'m:v1')),patch.object(middle,'_request',side_effect=TimeoutError):
            found,status=middle.semantic_candidates(self.db,'mine','unidentified objects')
            self.assertEqual(len(found),1)
            self.assertIn('unavailable',status['judge'])
        with patch.object(middle,'config',return_value={'enabled':True}),patch.object(middle,'queue_index'), \
             patch.object(middle,'embed',return_value=([[1,0]],'m:v2')):
            found,status=middle.semantic_candidates(self.db,'mine','unidentified objects')
            self.assertEqual(found,[])
        with middle.index(self.db) as cache:self.assertIsNone(cache.execute('SELECT vector FROM chunks').fetchone()[0])

    def test_index_omits_credentials_media_and_recursion(self):
        row={"content":json.dumps({"password":"hunter2","image_base64":"imagebytes","rows":[{"n":3}]}),"tool_calls":None,"tool_name":SQL}
        text = middle._safe_text(row)
        self.assertNotIn("hunter2",text)
        self.assertNotIn("imagebytes",text)
        self.assertIn('"n": 3',text)
        row["tool_name"]="session_context"
        self.assertEqual(middle._safe_text(row),"")

    def test_oversized_evidence_outlives_spill_cache_and_remains_scoped(self):
        raw=json.dumps({'rows':[{'n':5035}], 'tail':'x'*50000+' rare_original_value'})
        middle.archive_result(self.db,'mine','large',SQL,raw)
        self.add('<persisted-output>Cache preview only</persisted-output>',call='large')
        middle.ingest(self.db,'mine')
        self.assertEqual(self.read(tool_call_id='large',pointer='/rows/0/n')['content'],'5035')
        self.assertIn('rare_original_value',self.read(query='rare_original_value')['matches'][0]['excerpt'])
        self.assertIsNone(middle.original(self.db,'other','large'))
        self.assertEqual(middle.raw_matches(self.db,'other',['rare_original_value']),[])
        with middle.index(self.db) as cache:
            self.assertGreater(cache.execute("SELECT max(chunk_offset) FROM chunks").fetchone()[0],45000)

    def test_session_context_in_core_and_file_sets(self):
        from toolsets import _HERMES_CORE_TOOLS, TOOLSETS
        self.assertIn("session_context",_HERMES_CORE_TOOLS)
        self.assertIn("session_context",TOOLSETS["file"]["tools"])


class DeadlineTests(unittest.TestCase):
    def test_operator_budget_and_bridge_without_broad_extension(self):
        self.assertEqual(tool_deadline(420, [(SQL,{})],CONFIG),675)
        self.assertEqual(tool_deadline(420,[("tool_call",{"calls":[{"name":SQL,"arguments":{}}]})],CONFIG),675)
        self.assertEqual(tool_deadline(420,[("mcp__Warehouse__run_sql",{})],CONFIG),420)
        self.assertIsNone(tool_deadline(None,[(SQL,{})],CONFIG))

    def test_actual_parallel_executor_passes_configured_budget_to_batch(self):
        from agent import tool_executor as executor
        call=SimpleNamespace(name=SQL,args={},parse_error=None)
        agent=SimpleNamespace(_interrupt_requested=False,_touch_activity=Mock(),_should_emit_quiet_tool_messages=lambda:False,
                              _tool_progress_callback=None,quiet_mode=True)
        with patch("hermes_cli.config.load_config",return_value=CONFIG), \
             patch.object(executor,"_parse_tool_call",return_value=call), patch.object(executor,"_budget_for_agent"), \
             patch.object(executor,"_tool_progress_enabled",return_value=False), patch.object(executor,"_start_quiet_tool_spinner"), \
             patch.object(executor,"_ConcurrentBatch") as batch, patch.object(executor,"_append_batch_results",return_value=False):
            executor.execute_tool_calls_concurrent(agent,SimpleNamespace(tool_calls=[object()]),[],"task")
        self.assertEqual(batch.call_args.args[-1],675)

    def test_actual_sequential_executor_arms_extended_deadline(self):
        from agent import tool_executor as executor
        agent=SimpleNamespace(_tool_worker_threads=set(),_tool_worker_threads_lock=threading.Lock())
        with patch("hermes_cli.config.load_config",return_value=CONFIG), \
             patch("agent.terminal_approval_batch.take_prepared_call",return_value=None), \
             patch.object(executor,"_ConcurrentToolAuthorizationGate"), \
             patch.object(executor,"_run_agent_tool_execution_middleware",return_value="done"), \
             patch.object(executor,"_poll_sequential_future",return_value=("done","done")) as poll:
            executor._run_sequential_tool_execution_middleware(agent,function_name=SQL,function_args={},
                effective_task_id="task",tool_call_id="q",execute=lambda _:None)
        self.assertAlmostEqual(poll.call_args.args[3]-poll.call_args.args[4],675,delta=.1)

    def test_mcp_deadline_cause_is_not_fabricated_user_message(self):
        from tools import mcp_tool_handlers as handlers
        from tools.interrupt import set_interrupt
        set_interrupt(True,reason="tool batch deadline exceeded")
        try:
            with patch.object(handlers._loop,"_run_on_mcp_loop",side_effect=InterruptedError):
                result=json.loads(handlers._dispatch("Warehouse",SimpleNamespace(),"query",lambda:None,90,[],Mock()))
            self.assertIn("deadline",result["error"])
            self.assertNotIn("user sent",result["error"])
            self.assertEqual(result["error_type"],"tool_timeout")
        finally:
            set_interrupt(False)


if __name__ == "__main__":
    unittest.main()
