"""Pinned-native scheduler checks, including both DST boundaries and UTC persistence."""
import os,sys,tempfile
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo
sys.path.insert(0,os.environ.get('HERMES_SOURCE_ROOT','/opt/hermes'))
from cron import jobs

zone=ZoneInfo('America/New_York')
schedule=jobs.parse_schedule('CRON_TZ=America/New_York 0 8 * * 1')
assert schedule['timezone']=='America/New_York' and schedule['expr']=='0 8 * * 1'
# A full year's weekly chain, storing last_run in UTC as the real scheduler can.
anchor=datetime(2026,1,1,tzinfo=timezone.utc)
seen=[]
with patch.object(jobs,'get_timezone',lambda:ZoneInfo('UTC')):
 for _ in range(60):
  value=jobs.compute_next_run(schedule,anchor.isoformat())
  next_run=datetime.fromisoformat(value)
  local=next_run.astimezone(zone)
  assert local.weekday()==0 and local.hour==8 and local.minute==0,(anchor,value)
  assert next_run>anchor
  assert jobs._classify_stale_cron_next_run(schedule,next_run,next_run.astimezone(timezone.utc))==jobs.STALE_CRON_MATCH
  assert not jobs._repair_timezone_shifted_cron(SimpleNamespace(schedule=schedule))
  seen.append(local.utcoffset())
  anchor=next_run.astimezone(timezone.utc)+timedelta(minutes=10)
assert set(seen)=={timedelta(hours=-4),timedelta(hours=-5)}
for expr in ('CRON_TZ=Imaginary/City 0 8 * * 1','CRON_TZ=America/New_York every 1h'):
 try:jobs.parse_schedule(expr)
 except ValueError:pass
 else:raise AssertionError(expr)
# Existing jobs retain the installation clock.
with patch.object(jobs,'get_timezone',lambda:ZoneInfo('UTC')):
 next_run=datetime.fromisoformat(jobs.compute_next_run(jobs.parse_schedule('0 8 * * 1'),'2026-10-05T09:00:00+00:00'))
 assert next_run.hour==8 and next_run.utcoffset()==timedelta(0)
# Actual create/update/pause/resume use the same parsed schedule and preserve it.
with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,{'HERMES_HOME':root}):
 job=jobs.create_job('Timezone smoke test only; never run.','CRON_TZ=America/New_York 0 8 * * 1',paused=True)
 assert job['schedule']['timezone']=='America/New_York' and not job['enabled']
 changed=jobs.update_job(job['id'],{'schedule':'CRON_TZ=America/New_York 0 9 * * 1'})
 assert changed['schedule']['timezone']=='America/New_York'
 resumed=jobs.resume_job(job['id'])
 assert datetime.fromisoformat(resumed['next_run_at']).astimezone(zone).hour==9
 assert jobs.get_job(job['id'])['schedule']['timezone']=='America/New_York'
 jobs.remove_job(job['id'])
print('Hermes cron: 60 local Mondays, both DST offsets, UTC persistence, legacy schedules, create/edit/resume passed.')
