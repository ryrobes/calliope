"""Add per-job IANA clock schedules without changing the appliance timezone."""
import os
from pathlib import Path

path = Path(os.environ.get('HERMES_SOURCE_ROOT', '/opt/hermes')) / 'cron/jobs.py'
source = path.read_text()
if '# Calliope per-job cron timezone' in source:
    print('Per-job cron timezone already installed')
    raise SystemExit(0)


def replace(old, new):
    global source
    if source.count(old) != 1:
        raise SystemExit('Pinned Hermes cron timezone seam changed: ' + old[:100])
    source = source.replace(old, new)


replace('from hermes_time import get_timezone\n',
        'from hermes_time import get_timezone\nfrom zoneinfo import ZoneInfo\n')
replace('    schedule = schedule.strip()\n    original = schedule\n', '''    schedule = schedule.strip()
    # Calliope per-job cron timezone: persisted with the expression, never global.
    if schedule.startswith("CRON_TZ="):
        prefix, separator, expression = schedule.partition(" ")
        if not separator or not expression.strip():
            raise ValueError("CRON_TZ needs an IANA timezone and cron expression")
        name = prefix.removeprefix("CRON_TZ=")
        try:
            ZoneInfo(name)
        except (KeyError, ValueError) as exc:
            raise ValueError("Invalid cron timezone: " + name) from exc
        parsed = parse_schedule(expression)
        if parsed.get("kind") != "cron" or parsed.get("timezone"):
            raise ValueError("CRON_TZ is only supported for one cron expression")
        return {**parsed, "timezone": name, "display": f"{parsed['display']} ({name})"}
    original = schedule
''')
replace('        zone = get_timezone() or base_time.tzinfo\n',
        '        zone = ZoneInfo(schedule["timezone"]) if schedule.get("timezone") else (get_timezone() or base_time.tzinfo)\n')
replace('    expr = schedule.get("expr")\n    if not expr or not _ensure_croniter() or croniter is None:\n',
        '    if schedule.get("timezone"):\n        next_run_dt = next_run_dt.astimezone(ZoneInfo(schedule["timezone"]))\n'
        '    expr = schedule.get("expr")\n    if not expr or not _ensure_croniter() or croniter is None:\n')
replace('    now = d.scan.now\n    if not (\n        d.next_run_dt <= now\n',
        '    if d.schedule.get("timezone"):\n        return False  # Explicit job zones do not migrate with the installation timezone.\n'
        '    now = d.scan.now\n    if not (\n        d.next_run_dt <= now\n')
compile(source, str(path), 'exec')
path.write_text(source)
print('Installed per-job Hermes cron timezones')
