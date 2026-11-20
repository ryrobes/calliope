"""deploy/install.sh: the curl | bash installer shipped with every release.

The release asset and the copy inside the bundle are this file (shipping.py
copies it, and the release workflow uploads it). usecalliope.com serves that
same script; there is no second copy in the repo to keep in sync.
"""
import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'deploy' / 'install.sh'

# Answers OpenRouter, the GitHub releases API, and the releases/latest redirect.
CURL_STUB = r'''#!/bin/bash
url=""
out=""
i=0
argv=("$@")
n=${#argv[@]}
while [ "$i" -lt "$n" ]; do
  arg=${argv[$i]}
  case "$arg" in
    -o|--retry|--max-time|-w|-H|--header|-m)
      if [ "$arg" = -o ]; then out=${argv[$((i + 1))]:-}; fi
      i=$((i + 2))
      ;;
    --retry=*|--max-time=*|-o*)
      if [[ "$arg" == -o* ]]; then out=${arg#-o}; fi
      i=$((i + 1))
      ;;
    -*) i=$((i + 1)) ;;
    *) url=$arg; i=$((i + 1)) ;;
  esac
done
if [ -n "${CURL_LOG:-}" ]; then printf '%s\n' "$url" >> "$CURL_LOG"; fi
case "$url" in
  *openrouter.ai/api/v1/key*)
    printf '%s' "${KEY_BODY:-{}}" > "${out:-/dev/null}"
    printf '%s' "${KEY_STATUS:-200}"
    ;;
  *api.github.com/*/releases*)
    [ "${API_FAIL:-}" = 1 ] && exit 22
    printf '%s' "${API_BODY:-}"
    ;;
  */releases/latest)
    [ "${REDIRECT_FAIL:-}" = 1 ] && exit 22
    printf '%s' "${REDIRECT_URL:-}"
    ;;
  *) echo "unexpected curl: $url" >&2; exit 9 ;;
esac
'''

# Records every docker invocation. `config --services` prints FAKE_SERVICES.
DOCKER_STUB = r'''#!/bin/bash
printf '%s\n' "$*" >> "$DOCKER_LOG"
[ "${1:-}" = compose ] || { echo "unexpected docker: $*" >&2; exit 9; }
shift
cmd=""
while [ $# -gt 0 ]; do
  case "$1" in
    -f|--file)
      shift
      if [ "${1:-}" != compose.yaml ] && [ -f "${1:-}" ]; then
        printf 'OVERRIDE:\n' >> "$DOCKER_LOG"
        cat "$1" >> "$DOCKER_LOG"
      fi
      shift
      ;;
    --wait-timeout|--status) shift 2 ;;
    -*) shift ;;
    *) cmd=$1; shift; break ;;
  esac
done
case "$cmd" in
  config)
    # Intentional split: one service name per line.
    printf '%s\n' ${FAKE_SERVICES:-calliope postgres hermes origin bootstrap hindsight}
    ;;
  pull) exit 0 ;;
  up)
    joined=" $* "
    if [[ "$joined" == *" hindsight "* ]]; then
      if [[ "$joined" == *" calliope "* ]]; then
        echo "core wait included hindsight: $*" >&2
        exit 3
      fi
      [ "${HINDSIGHT_FAIL:-}" = 1 ] && exit 1
      exit 0
    fi
    [[ "$joined" == *" --wait "* ]] || { echo "missing --wait: $*" >&2; exit 4; }
    if [ -n "${CORE_FAIL:-}" ] && [[ "$joined" == *" ${CORE_FAIL} "* ]]; then
      exit 1
    fi
    exit 0
    ;;
  *) echo "unexpected compose $cmd $*" >&2; exit 9 ;;
esac
'''


def installer_body():
    return INSTALLER.read_text().rsplit('\nmain "$@"', 1)[0]


def run_helper(script, stdin=''):
    """Run shell code with the installer's functions loaded (main is not called)."""
    return subprocess.run(['bash', '-c', installer_body() + '\n' + script], input=stdin,
                          capture_output=True, text=True, check=True).stdout


def run_installer(script, tmp_path, env=None, check=True):
    """Run installer functions with curl/docker shadowed and Calliope env cleared."""
    bindir = tmp_path / 'bin'
    bindir.mkdir(exist_ok=True)
    merged = os.environ.copy()
    for key in list(merged):
        if key.startswith('CALLIOPE_') or key in ('OPENROUTER_API_KEY', 'OPENROUTER_UNFUNDED', 'HINDSIGHT_UNHEALTHY'):
            del merged[key]
    merged['PATH'] = f'{bindir}{os.pathsep}{merged["PATH"]}'
    merged.update(env or {})
    result = subprocess.run(['bash', '-c', installer_body() + '\n' + script],
                            capture_output=True, text=True, env=merged)
    if check and result.returncode != 0:
        raise AssertionError(
            f'exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}')
    return result


def write_stub(directory, name, text):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text)
    path.chmod(0o755)
    return path


def test_installer_parses_and_runs_main_last():
    subprocess.run(['bash', '-n', str(INSTALLER)], check=True)
    # A partial download must not run anything: everything is in functions until the last line.
    assert INSTALLER.read_text().rstrip().endswith('main "$@"')
    assert os.access(INSTALLER, os.X_OK)


@pytest.mark.parametrize('manifest, expected', [
    ('{\n  "calliope_version": "1.2.0-rc.4",\n  "bundle_version": "1.2.0-rc.4+bundle.1"\n}', '1.2.0-rc.4+bundle.1'),
    ('{"calliope_version": "1.2.0-rc.4", "images": ["calliope-server"]}', '1.2.0-rc.4'),
])
def test_release_version_prefers_the_bundle_version(manifest, expected):
    assert run_helper('release_version', manifest) == expected


def test_questions_read_the_terminal_not_stdin():
    # With curl | bash, stdin is the script itself.
    text = INSTALLER.read_text()
    assert 'read -r -p "$2${3:+ ${D}[$3]${N}}: " reply </dev/tty' in text
    assert 'read -r -s -p "$2: " reply </dev/tty' in text
    # The key is checked with OpenRouter as a header on stdin, never in argv.
    # -sS would print curl's own "curl: (7) ..." before the installer's warning.
    assert "curl -s -o \"$body\" -w '%{http_code}' -H @- https://openrouter.ai/api/v1/key" in text
    assert 'curl -sS ' not in text


def test_versions_compare_numerically():
    assert run_helper('version_ge 2.31.0 2.30 && echo yes') == 'yes\n'
    assert run_helper('version_ge 2.4.1 2.30 || echo no') == 'no\n'


def test_release_ships_the_installer():
    workflow = yaml.safe_load((ROOT / '.github/workflows/calliope-images.yml').read_text())
    steps = workflow['jobs']['bundle']['steps']
    publish = next(step for step in steps if step.get('name') == 'Publish the GitHub release')
    assert 'deploy/install.sh' in publish['run']
    assert '-linux-amd64.tar.gz.sha256' in publish['run']
    assert workflow['jobs']['bundle']['permissions']['contents'] == 'write'
    assert "'deploy/install.sh': 'install.sh'" in (ROOT / 'scripts/release/shipping.py').read_text()
    # gh release upload names the asset from this path's basename: install.sh.
    assert not (ROOT / 'install.sh').exists()


def test_hindsight_override_drops_only_that_dependency():
    text = run_helper('f=$(mktemp); defer_hindsight "$f"; cat "$f"; rm -f "$f"')
    # required:false would still start Hindsight and make --wait follow it.
    assert 'depends_on: !override' in text
    assert 'calliope:' in text
    assert 'service_healthy' in text
    assert 'hindsight:' not in text


@pytest.mark.parametrize('url, expected', [
    ('http://localhost:8080', '127.0.0.1:8080'),
    ('http://localhost:8080/', '127.0.0.1:8080'),
    ('http://127.0.0.1:9090', '127.0.0.1:9090'),
    ('http://localhost', '127.0.0.1:80'),
    ('http://127.0.0.1', '127.0.0.1:80'),
])
def test_local_http_bind_uses_the_url_port(url, expected):
    assert run_helper(f'local_http_bind {shlex.quote(url)}') == expected


@pytest.mark.parametrize('body, unfunded', [
    ('{"data":{"limit_remaining":0,"usage":1}}', True),
    ('{"data":{"limit_remaining":0.0}}', True),
    ('{"data":{"limit_remaining":null,"effective_budget":{"remaining_usd":0}}}', True),
    ('{"data":{"limit_remaining":74.5,"budgets":[{"remaining_usd":-0.01}]}}', True),
    ('{"data":{"limit_remaining":null,"usage":0}}', False),
    ('{"data":{"limit_remaining":12.5,"effective_budget":{"remaining_usd":12.5}}}', False),
    ('{"data":{"limit_remaining":null,"free_model_daily_requests":{"remaining":0}}}', False),
    ('{"data":{"limit_remaining":0.01}}', False),
])
def test_openrouter_unfunded_ignores_null_and_unrelated_zeros(tmp_path, body, unfunded):
    path = tmp_path / 'key.json'
    path.write_text(body)
    result = run_installer(
        f'openrouter_unfunded {shlex.quote(str(path))} && echo yes || echo no', tmp_path)
    assert result.stdout.strip() == ('yes' if unfunded else 'no')


def test_unattended_localhost_sets_loopback_bind_once(tmp_path):
    write_stub(tmp_path / 'bin', 'curl', CURL_STUB)
    settings = tmp_path / 'settings'
    script = f'''
CALLIOPE_YES=1
OPENROUTER_API_KEY=sk-or-test
CALLIOPE_ADMIN_EMAIL=owner@example.invalid
CALLIOPE_ADMIN_PASSWORD=test-only-strong-password
CALLIOPE_PUBLIC_URL=http://localhost:8080
KEY_BODY='{{"data":{{"limit_remaining":null,"usage":0}}}}'
collect_settings
printf 'bind=%s\\n' "$CALLIOPE_HTTP_BIND"
printf 'unfunded=%s\\n' "${{OPENROUTER_UNFUNDED-0}}"
mkdir -p {shlex.quote(str(settings))}
write_settings {shlex.quote(str(settings))}
'''
    result = run_installer(script, tmp_path, env={'KEY_BODY': '{"data":{"limit_remaining":null,"usage":0}}'})
    assert 'bind=127.0.0.1:8080\n' in result.stdout
    assert 'unfunded=0\n' in result.stdout
    lines = (settings / 'settings.env').read_text().splitlines()
    assert lines.count('CALLIOPE_HTTP_BIND=127.0.0.1:8080') == 1
    assert 'credits' not in result.stderr


def test_preset_http_bind_and_https_url_are_left_alone(tmp_path):
    write_stub(tmp_path / 'bin', 'curl', CURL_STUB)
    script = r'''
CALLIOPE_YES=1
OPENROUTER_API_KEY=sk-or-test
CALLIOPE_ADMIN_EMAIL=owner@example.invalid
CALLIOPE_ADMIN_PASSWORD=test-only-strong-password
CALLIOPE_PUBLIC_URL=http://127.0.0.1:9090
CALLIOPE_HTTP_BIND=0.0.0.0:9090
collect_settings
printf 'local=%s\n' "$CALLIOPE_HTTP_BIND"
CALLIOPE_PUBLIC_URL=https://calliope.example
CALLIOPE_HTTP_BIND=
EXTRA_SETTINGS=()
collect_settings
printf 'remote=%s\n' "${CALLIOPE_HTTP_BIND-<unset>}"
'''
    result = run_installer(script, tmp_path, env={'KEY_BODY': '{"data":{"limit_remaining":10}}'})
    assert 'local=0.0.0.0:9090\n' in result.stdout
    # Empty after the second call: configure.py applies the public default.
    assert 'remote=\n' in result.stdout


def test_unfunded_key_warns_before_install(tmp_path):
    write_stub(tmp_path / 'bin', 'curl', CURL_STUB)
    script = r'''
CALLIOPE_YES=1
OPENROUTER_API_KEY=sk-or-test
CALLIOPE_ADMIN_EMAIL=owner@example.invalid
CALLIOPE_ADMIN_PASSWORD=test-only-strong-password
CALLIOPE_PUBLIC_URL=http://localhost:8080
collect_settings
printf 'unfunded=%s\n' "${OPENROUTER_UNFUNDED-0}"
printf 'bind=%s\n' "$CALLIOPE_HTTP_BIND"
'''
    result = run_installer(script, tmp_path, env={
        'KEY_BODY': '{"data":{"limit_remaining":0,"usage":3}}'})
    assert 'unfunded=1\n' in result.stdout
    assert 'bind=127.0.0.1:8080\n' in result.stdout
    assert 'https://openrouter.ai/settings/credits' in result.stderr
    assert 'OpenRouter rejected' not in result.stderr


def test_rejected_key_still_stops_the_install(tmp_path):
    write_stub(tmp_path / 'bin', 'curl', CURL_STUB)
    script = r'''
CALLIOPE_YES=1
OPENROUTER_API_KEY=sk-or-test
CALLIOPE_ADMIN_EMAIL=owner@example.invalid
CALLIOPE_ADMIN_PASSWORD=test-only-strong-password
collect_settings
'''
    result = run_installer(script, tmp_path, env={'KEY_STATUS': '401'}, check=False)
    assert result.returncode != 0
    assert 'OpenRouter rejected that key' in result.stderr


def test_version_override_skips_the_network(tmp_path):
    log = tmp_path / 'curl.log'
    write_stub(tmp_path / 'bin', 'curl', CURL_STUB)
    result = run_installer('resolve_version; printf %s "$VERSION"\n', tmp_path, env={
        'CALLIOPE_VERSION': 'v3.1.0', 'CURL_LOG': str(log), 'API_FAIL': '1', 'REDIRECT_FAIL': '1'})
    assert result.stdout == '3.1.0'
    assert not log.exists() or log.read_text() == ''


@pytest.mark.parametrize('env, expected, saw_redirect', [
    ({'API_BODY': '[{"tag_name": "v1.2.0"}]', 'REDIRECT_FAIL': '1'}, '1.2.0', False),
    ({'API_BODY': '[{"tag_name": "v1.2.0-rc.4"}]'}, '1.2.0-rc.4', False),
    ({'API_FAIL': '1', 'REDIRECT_URL': 'https://github.com/ryrobes/calliope/releases/tag/v1.4.2'}, '1.4.2', True),
    ({'API_BODY': '{"message": "API rate limit exceeded"}',
      'REDIRECT_URL': 'https://github.com/ryrobes/calliope/releases/tag/v9.9.9/'}, '9.9.9', True),
    ({'API_FAIL': '1', 'REDIRECT_URL': 'https://github.com/ryrobes/calliope/releases/tag/v1.2.0-rc.1+bundle.2?draft=1'},
     '1.2.0-rc.1+bundle.2', True),
])
def test_latest_version_uses_the_api_then_the_redirect(tmp_path, env, expected, saw_redirect):
    log = tmp_path / 'curl.log'
    write_stub(tmp_path / 'bin', 'curl', CURL_STUB)
    result = run_installer('resolve_version; printf %s "$VERSION"\n', tmp_path,
                           env={**env, 'CURL_LOG': str(log)})
    assert result.stdout == expected
    calls = log.read_text().splitlines()
    assert any('api.github.com' in call for call in calls)
    assert any(call.endswith('/releases/latest') for call in calls) is saw_redirect


def test_version_lookup_fails_when_both_sources_fail(tmp_path):
    write_stub(tmp_path / 'bin', 'curl', CURL_STUB)
    result = run_installer('resolve_version; printf %s "$VERSION"\n', tmp_path,
                           env={'API_FAIL': '1', 'REDIRECT_FAIL': '1'}, check=False)
    assert result.returncode != 0
    assert 'CALLIOPE_VERSION' in result.stderr


def _stack(tmp_path, script, **env):
    write_stub(tmp_path / 'bin', 'docker', DOCKER_STUB)
    log = tmp_path / 'docker.log'
    directory = tmp_path / 'release'
    directory.mkdir()
    result = run_installer(script, tmp_path, env={
        'DOCKER_LOG': str(log), 'FAKE_SERVICES': 'calliope postgres hermes origin bootstrap hindsight', **env})
    return result, log.read_text()


def test_hindsight_failure_still_finishes(tmp_path):
    directory = tmp_path / 'release'
    result, log = _stack(tmp_path, f'''
bring_up {shlex.quote(str(directory))}
printf 'unhealthy=%s\n' "${{HINDSIGHT_UNHEALTHY-0}}"
''', HINDSIGHT_FAIL='1')
    assert 'unhealthy=1\n' in result.stdout
    ups = [line for line in log.splitlines() if ' up ' in line]
    assert len(ups) == 2
    core, memory = ups
    assert '--wait' in core and '--remove-orphans' in core and 'hindsight' not in core.split()
    assert 'calliope' in core.split() and 'bootstrap' in core.split()
    assert memory.split()[-1] == 'hindsight'
    assert '--wait' in memory
    assert 'depends_on: !override' in log
    assert "didn't come up healthy" not in result.stderr


def test_other_unhealthy_service_still_fails_the_install(tmp_path):
    directory = tmp_path / 'release'
    directory.mkdir()
    write_stub(tmp_path / 'bin', 'docker', DOCKER_STUB)
    log = tmp_path / 'docker.log'
    result = run_installer(f'''
start_stack {shlex.quote(str(directory))}
echo should-not-finish
''', tmp_path, env={
        'DOCKER_LOG': str(log), 'CORE_FAIL': 'postgres', 'HINDSIGHT_FAIL': '1',
    }, check=False)
    recorded = log.read_text()
    assert result.returncode != 0
    assert 'should-not-finish' not in result.stdout
    assert "didn't come up healthy" in result.stderr
    assert 're-running the installer resumes' in result.stderr
    ups = [line for line in recorded.splitlines() if ' up ' in line]
    assert len(ups) == 1
    assert 'hindsight' not in ups[0].split()


def test_unfunded_key_does_not_wait_on_hindsight(tmp_path):
    directory = tmp_path / 'release'
    result, log = _stack(tmp_path, f'''
bring_up {shlex.quote(str(directory))}
printf 'unhealthy=%s\n' "${{HINDSIGHT_UNHEALTHY-0}}"
''', OPENROUTER_UNFUNDED='1')
    assert 'unhealthy=1\n' in result.stdout
    memory = next(line for line in log.splitlines() if line.split()[-1:] == ['hindsight'])
    assert '--wait' not in memory


def test_upgrade_wait_uses_the_same_hindsight_split(tmp_path):
    # bring_up is what both install and upgrade call; a stack with no hindsight
    # still waits, and does not invent the override.
    directory = tmp_path / 'release'
    result, log = _stack(tmp_path, f'''
bring_up {shlex.quote(str(directory))}
printf 'unhealthy=%s\n' "${{HINDSIGHT_UNHEALTHY-0}}"
''', FAKE_SERVICES='calliope postgres')
    assert 'unhealthy=0\n' in result.stdout
    ups = [line for line in log.splitlines() if ' up ' in line]
    assert len(ups) == 1
    assert '--wait' in ups[0] and 'hindsight' not in ups[0]
    assert 'OVERRIDE:' not in log


def test_finish_summary_includes_hindsight_warning(tmp_path):
    root = tmp_path / 'install'
    (root / 'current').mkdir(parents=True)
    (root / 'current' / 'connections.json').write_text('{"web": "http://localhost:8080"}\n')
    script = f'''
ROOT={shlex.quote(str(root))}
CALLIOPE_PUBLIC_URL=http://localhost:8080
CALLIOPE_ADMIN_EMAIL=owner@example.invalid
GENERATED_PASSWORD=generated-password-1
HINDSIGHT_UNHEALTHY=1
finish installed
'''
    warned = run_installer(script, tmp_path)
    assert 'http://localhost:8080' in warned.stdout
    assert 'owner@example.invalid' in warned.stdout
    assert 'generated-password-1' in warned.stdout
    assert 'Hindsight did not become healthy' in warned.stdout
    assert 'https://openrouter.ai/settings/credits' in warned.stdout
    assert 'docker compose logs --tail 100 hindsight' in warned.stdout
    assert 'docker compose up -d --wait hindsight' in warned.stdout
    quiet = run_installer(script.replace('HINDSIGHT_UNHEALTHY=1\n', ''), tmp_path)
    assert 'Hindsight did not become healthy' not in quiet.stdout
    assert 'http://localhost:8080' in quiet.stdout


def test_key_check_does_not_print_curl_transport_errors(tmp_path):
    write_stub(tmp_path / 'bin', 'curl', '''#!/bin/bash
for arg in "$@"; do
  if [ "$arg" = -sS ]; then
    echo "curl: (7) Failed to connect" >&2
  fi
done
exit 7
''')
    result = run_installer(r'''
CALLIOPE_YES=1
OPENROUTER_API_KEY=sk-or-test
CALLIOPE_ADMIN_EMAIL=owner@example.invalid
CALLIOPE_ADMIN_PASSWORD=test-only-strong-password
CALLIOPE_PUBLIC_URL=https://calliope.example
collect_settings
printf 'continued\n'
''', tmp_path)
    assert 'continued' in result.stdout
    assert 'curl:' not in result.stderr
    assert "Couldn't check the key with OpenRouter" in result.stderr


def test_releases_page_without_a_tag_is_a_lookup_failure(tmp_path):
    write_stub(tmp_path / 'bin', 'curl', CURL_STUB)
    result = run_installer('resolve_version; printf %s "$VERSION"\n', tmp_path, env={
        'API_FAIL': '1', 'REDIRECT_URL': 'https://github.com/ryrobes/calliope/releases'}, check=False)
    assert result.returncode != 0
    assert "Unexpected release version" not in result.stderr
    assert 'set CALLIOPE_VERSION' in result.stderr


def test_postgres_backup_is_owner_readable_only(tmp_path):
    write_stub(tmp_path / 'bin', 'docker', '''#!/bin/bash
if [[ "$*" == *pg_dumpall* ]]; then
  printf 'calliope-dump\n'
  exit 0
fi
echo "unexpected docker: $*" >&2
exit 9
''')
    directory = tmp_path / 'release'
    directory.mkdir()
    dest = tmp_path / 'backups' / 'pg_dumpall.sql.gz'
    dest.parent.mkdir()
    run_installer(
        f'umask 022; backup_postgres_dump {shlex.quote(str(directory))} {shlex.quote(str(dest))}',
        tmp_path)
    assert dest.stat().st_mode & 0o777 == 0o600
    import gzip
    assert gzip.open(dest, 'rt').read() == 'calliope-dump\n'


def test_unattended_install_generates_and_prints_admin_password(tmp_path):
    write_stub(tmp_path / 'bin', 'curl', CURL_STUB)
    settings = tmp_path / 'settings'
    root = tmp_path / 'install'
    (root / 'current').mkdir(parents=True)
    (root / 'current' / 'connections.json').write_text('{"web": "http://localhost:8080"}\n')
    result = run_installer(f'''
CALLIOPE_YES=1
OPENROUTER_API_KEY=sk-or-test
CALLIOPE_ADMIN_EMAIL=owner@example.invalid
unset CALLIOPE_ADMIN_PASSWORD
CALLIOPE_PUBLIC_URL=http://localhost:8080
collect_settings
printf 'pass=%s\\n' "$CALLIOPE_ADMIN_PASSWORD"
printf 'same=%s\\n' "$([ "$GENERATED_PASSWORD" = "$CALLIOPE_ADMIN_PASSWORD" ] && echo yes)"
mkdir -p {shlex.quote(str(settings))}
write_settings {shlex.quote(str(settings))}
ROOT={shlex.quote(str(root))}
finish installed
''', tmp_path, env={'KEY_BODY': '{"data":{"limit_remaining":10}}'})
    password = next(line.removeprefix('pass=') for line in result.stdout.splitlines() if line.startswith('pass='))
    assert len(password) == 20 and password.isalnum()
    assert 'same=yes' in result.stdout
    assert f'CALLIOPE_ADMIN_PASSWORD={password}' in (settings / 'settings.env').read_text()
    assert password in result.stdout.split('Calliope is installed', 1)[-1]


def test_explicit_admin_password_is_not_replaced(tmp_path):
    write_stub(tmp_path / 'bin', 'curl', CURL_STUB)
    settings = tmp_path / 'settings'
    root = tmp_path / 'install'
    (root / 'current').mkdir(parents=True)
    (root / 'current' / 'connections.json').write_text('{"web": "http://localhost:8080"}\n')
    chosen = 'test-only-strong-password'
    result = run_installer(f'''
CALLIOPE_YES=1
OPENROUTER_API_KEY=sk-or-test
CALLIOPE_ADMIN_EMAIL=owner@example.invalid
CALLIOPE_ADMIN_PASSWORD={chosen}
CALLIOPE_PUBLIC_URL=http://localhost:8080
collect_settings
printf 'pass=%s\\n' "$CALLIOPE_ADMIN_PASSWORD"
printf 'generated=%s\\n' "${{GENERATED_PASSWORD-<unset>}}"
mkdir -p {shlex.quote(str(settings))}
write_settings {shlex.quote(str(settings))}
ROOT={shlex.quote(str(root))}
finish installed
''', tmp_path, env={'KEY_BODY': '{"data":{"limit_remaining":10}}'})
    assert f'pass={chosen}' in result.stdout
    assert 'generated=' in result.stdout
    assert f'CALLIOPE_ADMIN_PASSWORD={chosen}' in (settings / 'settings.env').read_text()
    summary = result.stdout.split('Calliope is installed', 1)[-1]
    assert 'Password' not in summary
