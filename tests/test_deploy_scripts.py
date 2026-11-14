"""The server's deploy files and backup scripts (deploy/gcp, backup_sheets.py): the checks that need no server."""
import glob
import importlib.util
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GCP = os.path.join(ROOT, 'deploy', 'gcp')
UNITS = os.path.join(GCP, 'systemd')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(*parts):
    with open(os.path.join(ROOT, *parts)) as f:
        return f.read()


# --- shell scripts ---

def test_deploy_sh_is_posix_and_the_server_scripts_parse():
    """deploy.sh is run with "sh" (the drapps-deploy wrapper); the server's scripts are bash."""
    assert subprocess.run(['sh', '-n', os.path.join(GCP, 'deploy.sh')]).returncode == 0
    for name in ('lib.sh', 'on-server.sh', 'update.sh'):
        assert subprocess.run(['bash', '-n', os.path.join(GCP, name)]).returncode == 0, name


def test_deploy_sh_never_pipes_the_test_run():
    """A pipe after pytest hides its exit code: a failing test run must stop the deploy."""
    for line in _read('deploy', 'gcp', 'deploy.sh').splitlines():
        if 'pytest' in line and not line.lstrip().startswith('#'):
            assert '|' not in line.replace('||', ''), line


# --- job units ---

def _units(ext):
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(UNITS, f'*.{ext}')))


def test_every_unit_file_has_the_name_the_deploy_installs_and_removes_by():
    for name in os.listdir(UNITS):
        assert re.fullmatch(r'drscca-[a-z0-9@-]+\.(service|timer|path)', name), name


def test_every_timer_and_path_has_its_service_and_can_be_enabled():
    for name in _units('timer') + _units('path'):
        assert os.path.exists(os.path.join(UNITS, name.rsplit('.', 1)[0] + '.service')), name
        assert '[Install]' in _read('deploy', 'gcp', 'systemd', name), name


def test_every_job_alerts_on_failure_and_the_alert_knows_it():
    alert = _load('drscca_alert', os.path.join(GCP, 'alert.py'))
    for name in _units('service'):
        if name == 'drscca-alert@.service':
            continue
        assert 'OnFailure=drscca-alert@%n.service' in _read('deploy', 'gcp', 'systemd', name), name
        assert name in alert.WHAT, f'{name} has no description in alert.py'
    for name in alert.WHAT:
        assert name in _units('service'), f'alert.py describes {name}, which is not a unit'


def test_alert_unit_gets_the_host_key_path_like_the_other_host_jobs():
    line = 'Environment=GOOGLE_SA_KEY_HOST=/opt/drscca/data/secrets/drapps-sa.json'
    for name in ('drscca-alert@.service', 'drscca-qbo.service', 'drscca-officials.service'):
        assert line in _read('deploy', 'gcp', 'systemd', name), name


def test_officials_job_names_the_real_container():
    assert 'Environment=APP_CONTAINER=drapps' in _read('deploy', 'gcp', 'systemd', 'drscca-officials.service')
    assert "'event-drscca'" not in _read('tools', 'officials_queue.py')
    assert 'container_name: drapps' in _read('deploy', 'gcp', 'docker-compose.yml')


# --- alert.py ---

class _FakeUtils:
    """Just enough of utils.py: send_mail starts a thread and reports through a logger, like the real one."""

    def __init__(self, works):
        import logging
        import threading
        import types
        self.mod = types.ModuleType('utils')
        self.mod.threading = threading
        log = logging.getLogger('utils')
        self.mod.sent = sent = []

        def send_mail(subject, body, recipient=None):
            def go():
                if works:
                    sent.append((subject, recipient))
                    log.info(f'Email sent to {recipient}: {subject}')
                else:
                    log.error('Failed to send email: no key')
            self.mod.threading.Thread(target=go).start()
        self.mod.send_mail = send_mail


@pytest.mark.parametrize('works, code', [(True, 0), (False, 1)])
def test_alert_waits_for_the_email_and_fails_when_it_does_not_go(monkeypatch, works, code):
    alert = _load('drscca_alert', os.path.join(GCP, 'alert.py'))
    fake = _FakeUtils(works)
    monkeypatch.setitem(sys.modules, 'utils', fake.mod)
    monkeypatch.setattr(alert.subprocess, 'run', lambda *a, **k: type('R', (), {'stdout': 'log line'})())
    assert alert.main(['alert.py', 'drscca-qbo.service']) == code
    assert fake.mod.sent == ([('drapps: nightly QuickBooks sync failed', 'admin@drscca.org')] if works else [])
    assert fake.mod.threading.__name__ == 'threading'       # put back afterwards


# --- backup_sheets.py ---

def test_one_bad_sheet_does_not_stop_the_others(tmp_path, monkeypatch):
    import backup_sheets as B

    class Session:
        def get(self, url, **kw):
            bad = '/bad-id/' in url

            class R:
                content = b'xlsx'

                def raise_for_status(self):
                    if bad:
                        raise RuntimeError('403')
            return R()

    monkeypatch.setattr(B, 'OUT', str(tmp_path / 'backups'))
    monkeypatch.setattr(B, 'SHEETS', (('first', 'T_FIRST'), ('broken', 'T_BROKEN'), ('last', 'T_LAST'), ('unset', 'T_UNSET')))
    monkeypatch.setattr(B.service_account.Credentials, 'from_service_account_file', lambda *a, **k: object())
    monkeypatch.setattr(B, 'AuthorizedSession', lambda creds: Session())
    monkeypatch.setenv('GOOGLE_SERVICE_ACCOUNT_FILE', 'unused')
    monkeypatch.setenv('T_FIRST', 'a-id')
    monkeypatch.setenv('T_BROKEN', 'bad-id')
    monkeypatch.setenv('T_LAST', 'c-id')
    (tmp_path / 'records-edits.json').write_text('[]')

    assert B.main() == ['broken']
    saved = sorted(os.listdir(tmp_path / 'backups'))
    assert [n.split('-', 3)[3] for n in saved] == ['first.xlsx', 'last.xlsx', 'records-edits.json']


# --- secrets_backup.py ---

def test_backup_copy_must_be_readable_by_admin_only():
    S = _load('drscca_secrets_backup', os.path.join(GCP, 'secrets_backup.py'))
    admin = {'type': 'user', 'emailAddress': 'Admin@drscca.org', 'role': 'organizer'}
    allowed = {'admin@drscca.org'}
    assert S.others([admin], allowed) == []
    assert S.others([admin, {'type': 'domain', 'domain': 'drscca.org'}], allowed) == ['drscca.org']
    assert S.others([admin, {'type': 'anyone'}], allowed) == ['anyone']
    assert S.others([admin, {'type': 'user', 'emailAddress': 'someone@example.org'}], allowed) == ['someone@example.org']
    assert S.others([admin, {'type': 'group', 'emailAddress': 'board@example.org'}], allowed) == ['board@example.org']


# --- Caddyfile, compose, Dockerfile ---

def test_every_site_sends_the_security_headers():
    caddy = _read('deploy', 'gcp', 'Caddyfile')
    snippet = caddy[caddy.index('(security_headers)'):]
    snippet = snippet[:snippet.index('\n}\n')]
    for want in ('Strict-Transport-Security', 'X-Content-Type-Options "nosniff"', 'X-Frame-Options "DENY"',
                 'Referrer-Policy "strict-origin-when-cross-origin"', '-Server'):
        assert want in snippet, want
    assert 'Content-Security-Policy' not in snippet      # pages use inline scripts
    sites = re.findall(r'^([a-z0-9.-]+\.[a-z]+) \{\n(.*?)^\}', caddy, flags=re.M | re.S)
    assert len(sites) == 3
    for host, body in sites:
        assert 'import security_headers' in body, host


def test_production_never_starts_the_dev_server():
    for text in (_read('Dockerfile'), _read('deploy', 'gcp', 'docker-compose.yml')):
        assert '--timeout' in text and '120' in text
    assert 'python", "app.py' not in _read('Dockerfile')
    assert '"127.0.0.1:5858:5858"' in _read('docker-compose.debug.yml')


# --- install_units (deploy/gcp/lib.sh): run for real against temp folders, with a stand-in systemctl ---

def _install_units(tmp_path, src, dst):
    fake = tmp_path / 'bin'
    fake.mkdir(exist_ok=True)
    calls = tmp_path / 'systemctl.log'
    (fake / 'systemctl').write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    (fake / 'systemctl').chmod(0o755)
    r = subprocess.run(['bash', '-c', 'set -Eeuo pipefail; . "$0"; install_units "$1" "$2" "$3"',
                        os.path.join(GCP, 'lib.sh'), str(src), str(dst), str(tmp_path / 'attic')],
                       env=dict(os.environ, PATH=f'{fake}:{os.environ["PATH"]}'), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = calls.read_text().splitlines() if calls.exists() else []
    if calls.exists():
        calls.unlink()
    return out


def test_deploy_turns_on_every_timer_and_path_and_removes_only_its_own_old_units(tmp_path):
    dst = tmp_path / 'system'
    dst.mkdir()
    (dst / 'drscca-livetiming-archive.timer').write_text('old')          # ours, gone from the repo: removed
    (dst / 'drscca-livetiming-archive.service').write_text('old')
    (dst / 'docker.service').write_text('not ours')                       # never touched
    (dst / 'drscca-notes.txt').write_text('not a unit')
    (dst / 'drscca-qbo.service.d').mkdir()                                # a folder: never touched
    (dst / 'drscca-linked.service').symlink_to(dst / 'docker.service')    # a link: never touched
    (dst / 'drscca-qbo.timer').write_text('stale copy')                   # ours, changed in the repo: replaced

    calls = _install_units(tmp_path, UNITS, dst)

    left = set(os.listdir(dst))
    assert not {'drscca-livetiming-archive.timer', 'drscca-livetiming-archive.service'} & left
    assert {'docker.service', 'drscca-notes.txt', 'drscca-qbo.service.d', 'drscca-linked.service'} <= left
    assert set(os.listdir(UNITS)) <= left
    assert (dst / 'drscca-qbo.timer').read_text() == _read('deploy', 'gcp', 'systemd', 'drscca-qbo.timer')
    assert len(os.listdir(tmp_path / 'attic')) == 2                      # copies of what was removed
    assert 'disable --now drscca-livetiming-archive.timer' in calls
    assert 'daemon-reload' in calls
    for name in _units('timer') + _units('path'):
        assert f'enable --now {name}' in calls, name
        assert f'restart {name}' in calls, name                          # all were new or changed this time
    assert not any('docker.service' in c or 'drscca-linked' in c for c in calls)

    # A second run changes nothing: no removals, no restarts, the same units turned on.
    again = _install_units(tmp_path, UNITS, dst)
    assert not [c for c in again if c.startswith(('restart', 'disable'))]
    assert [c for c in again if c.startswith('enable')] == [c for c in calls if c.startswith('enable')]


def test_offsite_backup_names_and_the_one_harmless_warning():
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location('offsite', os.path.join(os.path.dirname(__file__), '..', 'deploy', 'gcp', 'offsite_backup.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.folder_name('DRSCCA Apps') == 'DRSCCA Apps' and m.folder_name('a/b:c') == 'a_b_c'
    assert m.HARMLESS.match('error: incomplete metadata for /var/lib/drscca-offsite/drives/A/b.pdf: xattr.list /var/lib/drscca-offsite/drives/A/b.pdf: input/output error')
    assert m.HARMLESS.match('incomplete metadata for /x/b.pdf: xattr.list /x/b.pdf: input/output error')
    assert not m.HARMLESS.match('error: incomplete metadata for /x/b.pdf: lstat /x/b.pdf: input/output error')
    assert not m.HARMLESS.match('error: open /var/lib/drscca-offsite/drives/A/b.pdf: input/output error')
