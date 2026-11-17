"""Let Calliope fork a session at a moment and keep the source open (see hermes-session-fork.py)."""
from pathlib import Path

p = Path('/opt/hermes/gateway/platforms/api_server.py')
s = p.read_text()

old = '''        await asyncio.to_thread(db.end_session, source_id, "branched")
        messages = await asyncio.to_thread(db.get_messages, source_id)
        await asyncio.to_thread(db.replace_messages, fork_id, messages)
'''
assert s.count(old) == 1, 'Session fork seam changed'
s = s.replace(old, '''        from gateway import calliope_session_fork as _calliope_fork
        if not _calliope_fork.keeps_source(body):
            await asyncio.to_thread(db.end_session, source_id, "branched")
        messages = await asyncio.to_thread(_calliope_fork.messages_for_fork, db, source_id, body)
        await asyncio.to_thread(db.replace_messages, fork_id, messages)
''')

old = '''"session_chat_streaming": True, "session_fork": True,'''
assert s.count(old) == 1, 'Capability flags seam changed'
s = s.replace(old, '''"session_chat_streaming": True, "session_fork": True, "calliope_session_fork": True,''')
p.write_text(s)
