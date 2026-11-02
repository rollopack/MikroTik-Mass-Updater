import argparse
import json
import logging
import queue
import sys
import threading
import time

import pytest

logging.disable(logging.CRITICAL)

import mkmassupdate
from mkmassupdate import (
    MassUpdater,
    RetryStateMissing,
    UpdaterConfig,
    _create_run_dir,
    _parse_args,
    _setup_logger,
)
from mkreport import HostResult, build_summary, render_state


def _make_args(**overrides):
    defaults = dict(
        username='admin',
        password='test',
        threads=1,
        timeout=5,
        ip_list='list.txt',
        port=8728,
        ssl_port=8729,
        update_check_attempts=1,
        update_check_delay=0.01,
        no_colors=True,
        dry_run=False,
        start_line=1,
        debug=False,
        cloud_password=None,
        upgrade_firmware=False,
        ssl=False,
        custom_commands=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class RecordingApi:
    """Fake librouteros connection that records the commands it receives."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}
        self.closed = False

    def __call__(self, command, **params):
        name = command[0] if isinstance(command, tuple) else command
        self.calls.append(name)
        return self.responses.get(name, [])

    def path(self, *parts):
        return self

    def close(self):
        self.closed = True


def _connected(monkeypatch, api):
    monkeypatch.setattr(mkmassupdate, '_connect_to_router', lambda *a, **k: api)


def _host(ip='10.0.0.1', port=8728, ssl=False):
    return (ip, port, None, None, ssl)


class TestUpdaterConfig:
    def test_defaults_when_attributes_are_missing(self):
        config = UpdaterConfig.from_args(argparse.Namespace(username='u', password='p'))
        assert config.threads == 5
        assert config.report is True
        assert config.inventory is False
        assert config.only is None

    def test_copies_namespace_values(self):
        config = UpdaterConfig.from_args(
            argparse.Namespace(username='u', password='p', inventory=True, only='10.0.0.1', report=False)
        )
        assert config.inventory is True
        assert config.only == '10.0.0.1'
        assert config.report is False


class TestUpdaterEventsAndCancellation:
    def test_run_reports_startup_error_in_completion_event(self, tmp_path):
        events = queue.Queue()
        updater = MassUpdater(
            _make_args(ip_list=str(tmp_path / 'missing-hosts.txt')),
            event_queue=events,
        )

        assert updater.run() is True

        completion = events.get_nowait()
        assert completion['type'] == 'completed'
        assert 'IP list file not found' in completion['error']

    def test_run_emits_host_progress_and_completion_events(self, monkeypatch, tmp_path):
        events = queue.Queue()
        updater = MassUpdater(
            _make_args(),
            event_queue=events,
        )
        monkeypatch.setattr(updater, '_load_ip_list', lambda: ['10.0.0.1\n'])
        monkeypatch.setattr(
            updater,
            '_process_host',
            lambda host, commands: HostResult(
                ip='10.0.0.1', port=8728, success=True, status='ok',
                identity='edge-1', cloud_backup_key='sensitive-key',
                log_lines=['Host: 10.0.0.1\n  Done\n'],
            ),
        )
        monkeypatch.setattr(updater, '_finalize', lambda: False)

        assert updater.run() is False

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        event_types = [event['type'] for event in emitted]
        host_event = next(event for event in emitted if event['type'] == 'host_result')
        assert event_types == [
            'hosts_loaded', 'host_started', 'host_result', 'progress', 'completed'
        ]
        assert host_event['host']['identity'] == 'edge-1'
        assert 'cloud_backup_key' not in host_event['host']
        assert emitted[-1]['summary']['total'] == 1

    def test_host_log_lines_are_streamed_and_sensitive_key_is_redacted(self, monkeypatch):
        events = queue.Queue()
        api = RecordingApi({'/system/identity/print': [{'name': 'edge-1'}]})
        _connected(monkeypatch, api)
        updater = MassUpdater(_make_args(), event_queue=events)
        updater._process_host(_host(), [])

        emitted = []
        while not events.empty():
            emitted.append(events.get_nowait())
        lines = [event for event in emitted if event['type'] == 'log_line']
        assert any('Host: 10.0.0.1' in event['message'] for event in lines)
        assert any('Identity: edge-1' in event['message'] for event in lines)
        assert mkmassupdate.MassUpdater._redact_log(
            'Secret Download Key: private-value'
        ) == 'Secret Download Key: [redacted]'

    def test_request_stop_discards_pending_hosts_and_joins_active_work(self, monkeypatch, tmp_path):
        events = queue.Queue()
        first_started = threading.Event()
        queue_loaded = threading.Event()
        finish_first = threading.Event()
        ip_list = tmp_path / 'hosts.txt'
        ip_list.write_text('10.0.0.1\n10.0.0.2\n10.0.0.3\n', encoding='utf-8')
        updater = MassUpdater(
            _make_args(ip_list=str(ip_list)), event_queue=events,
        )
        original_populate = updater._populate_queue

        def populate_and_signal(lines):
            original_populate(lines)
            queue_loaded.set()

        def process(host, commands):
            first_started.set()
            assert finish_first.wait(timeout=3)
            return HostResult(ip=host[0], port=host[1], success=True, status='ok')

        monkeypatch.setattr(updater, '_populate_queue', populate_and_signal)
        monkeypatch.setattr(updater, '_process_host', process)
        monkeypatch.setattr(updater, '_finalize', lambda: False)
        run_thread = threading.Thread(target=updater.run)
        run_thread.start()

        assert first_started.wait(timeout=3)
        assert queue_loaded.wait(timeout=3)
        updater.request_stop()
        finish_first.set()
        run_thread.join(timeout=3)

        assert not run_thread.is_alive()
        assert [result.ip for result in updater.aggregated_results] == ['10.0.0.1']
        assert updater.q.unfinished_tasks == 0
        completed = [
            event for event in list(events.queue) if event['type'] == 'completed'
        ][0]
        assert completed['cancelled'] is True


class TestInventoryMode:
    def _api(self):
        return RecordingApi(
            {
                '/system/identity/print': [{'name': 'edge-1'}],
                '/system/routerboard/print': [
                    {
                        'board-name': 'RB750Gr3',
                        'serial-number': 'HEX123',
                        'current-firmware': '7.14.3',
                        'upgrade-firmware': '7.15.1',
                    }
                ],
                '/system/resource/print': [
                    {'version': '7.14.3', 'architecture-name': 'arm', 'uptime': '2d'}
                ],
            }
        )

    def test_runs_only_read_commands(self, monkeypatch):
        api = self._api()
        _connected(monkeypatch, api)
        args = _make_args(inventory=True, cloud_password='cloud', upgrade_firmware=True)
        updater = MassUpdater(args)

        result = updater._process_host(_host(), [])

        assert api.calls == [
            '/system/identity/print',
            '/system/routerboard/print',
            '/system/resource/print',
        ]
        assert result.success is True
        assert result.status == 'ok'
        assert api.closed is True

    def test_collects_routerboard_data(self, monkeypatch):
        api = self._api()
        _connected(monkeypatch, api)
        updater = MassUpdater(_make_args(inventory=True))

        result = updater._process_host(_host(), [])

        assert result.identity == 'edge-1'
        assert result.model == 'RB750Gr3'
        assert result.serial_number == 'HEX123'
        assert result.firmware_current == '7.14.3'
        assert result.firmware_upgrade == '7.15.1'
        assert result.routeros_version == '7.14.3'
        assert result.architecture == 'arm'
        assert result.uptime == '2d'

    def test_skips_custom_commands(self, monkeypatch):
        api = self._api()
        _connected(monkeypatch, api)
        updater = MassUpdater(_make_args(inventory=True))

        result = updater._process_host(_host(), ['/ip/firewall/filter/print'])

        assert '/ip/firewall/filter/print' not in api.calls
        assert any('custom commands skipped' in line for line in result.log_lines)

    def test_records_duration_and_status(self, monkeypatch):
        _connected(monkeypatch, self._api())
        updater = MassUpdater(_make_args(inventory=True))
        result = updater._process_host(_host(), [])
        assert result.duration_s is not None
        assert result.duration_s >= 0
        assert result.status == 'ok'


class TestFullMode:
    def test_failed_firmware_check_does_not_install_available_routeros_update(self, monkeypatch):
        api = RecordingApi(
            {
                '/system/package/update/print': [
                    {
                        'status': 'New version is available',
                        'installed-version': '7.14.3',
                        'latest-version': '7.15.1',
                    }
                ]
            }
        )
        monkeypatch.setattr(mkmassupdate.time, 'sleep', lambda _: None)

        outcome = mkmassupdate._check_and_process_updates(
            api, [], dry_run=False, check_attempts=1, check_delay=0,
            allow_install=False,
        )

        assert outcome.available is True
        assert outcome.reboot_required is False
        assert api.calls == [
            '/system/package/update/check-for-updates',
            '/system/package/update/print',
            '/system/package/update/print',
        ]

    def test_runs_backup_firmware_and_updates(self, monkeypatch):
        calls = []
        api = RecordingApi({'/system/identity/print': [{'name': 'edge-1'}]})
        _connected(monkeypatch, api)

        def fake_backup(api, password, entry_lines, dry_run=False):
            calls.append('backup')
            return mkmassupdate.CloudBackupOutcome(True, secret_key='key-1')

        def fake_firmware(api, entry_lines, dry_run=False):
            calls.append('firmware')
            return mkmassupdate.FirmwareOutcome(True, '7.14.3', '7.15.1')

        def fake_updates(api, entry_lines, dry_run, attempts, delay, allow_install=True):
            calls.append('updates')
            return mkmassupdate.UpdateOutcome(
                ok=True, installed_version='7.14.3', latest_version='7.15.1', available=True
            )

        def fake_reboot(api, entry_lines):
            calls.append('reboot')

        monkeypatch.setattr(mkmassupdate, '_perform_cloud_backup', fake_backup)
        monkeypatch.setattr(mkmassupdate, '_perform_firmware_upgrade', fake_firmware)
        monkeypatch.setattr(mkmassupdate, '_check_and_process_updates', fake_updates)
        monkeypatch.setattr(mkmassupdate, '_reboot_router', fake_reboot)

        args = _make_args(cloud_password='cloud', upgrade_firmware=True)
        result = MassUpdater(args)._process_host(_host(), [])

        assert calls == ['backup', 'firmware', 'updates', 'reboot']
        assert result.success is True
        assert result.cloud_backup_key == 'key-1'
        assert result.backup_ok is True
        assert result.update_available is True
        assert result.latest_version == '7.15.1'

    def test_inventory_ignores_backup_and_update_flags(self, monkeypatch):
        calls = []
        _connected(monkeypatch, RecordingApi())
        monkeypatch.setattr(
            mkmassupdate, '_perform_cloud_backup',
            lambda *a, **k: calls.append('backup') or mkmassupdate.CloudBackupOutcome(True),
        )
        monkeypatch.setattr(
            mkmassupdate, '_check_and_process_updates',
            lambda *a, **k: calls.append('updates') or mkmassupdate.UpdateOutcome(ok=True),
        )
        args = _make_args(inventory=True, cloud_password='cloud', upgrade_firmware=True)
        MassUpdater(args)._process_host(_host(), [])
        assert calls == []

    def test_checks_routeros_after_firmware_error_without_installing(self, monkeypatch):
        api = RecordingApi()
        _connected(monkeypatch, api)
        monkeypatch.setattr(
            mkmassupdate,
            '_perform_firmware_upgrade',
            lambda *a, **k: mkmassupdate.FirmwareOutcome(False),
        )
        update_calls = []

        def check_updates(*args, **kwargs):
            update_calls.append(kwargs)
            return mkmassupdate.UpdateOutcome(
                ok=True,
                installed_version='7.25',
                latest_version='7.25',
                available=False,
            )

        monkeypatch.setattr(mkmassupdate, '_check_and_process_updates', check_updates)
        result = MassUpdater(_make_args(upgrade_firmware=True))._process_host(_host(), [])

        assert result.success is False
        assert result.update_available is False
        assert update_calls == [{'allow_install': False}]

    def test_connect_error_marks_failure(self, monkeypatch):
        def refuse(*args, **kwargs):
            raise TimeoutError()

        monkeypatch.setattr(mkmassupdate, '_connect_to_router', refuse)
        result = MassUpdater(_make_args())._process_host(_host(), [])
        assert result.success is False
        assert result.status == 'failed'
        assert 'timed out' in (result.error or '')


class TestHostSelection:
    def _list_file(self, tmp_path, content):
        path = tmp_path / 'list.txt'
        path.write_text(content, encoding='utf-8')
        return path

    def test_only_selects_matching_hosts(self, tmp_path):
        path = self._list_file(
            tmp_path,
            "# comment\n10.0.0.1\n10.0.0.2|admin|secret\n\n10.0.0.3|SSL\n",
        )
        updater = MassUpdater(_make_args(ip_list=str(path), only='10.0.0.2'))
        assert updater._load_ip_list() == ['10.0.0.2|admin|secret\n']

    def test_only_accepts_multiple_hosts(self, tmp_path):
        path = self._list_file(tmp_path, "10.0.0.1\n10.0.0.2\n10.0.0.3\n")
        updater = MassUpdater(_make_args(ip_list=str(path), only='10.0.0.1, 10.0.0.3'))
        assert [MassUpdater._line_ip(line) for line in updater._load_ip_list()] == [
            '10.0.0.1',
            '10.0.0.3',
        ]

    def test_only_without_match_returns_nothing(self, tmp_path):
        path = self._list_file(tmp_path, "10.0.0.1\n")
        updater = MassUpdater(_make_args(ip_list=str(path), only='192.0.2.99'))
        assert updater._load_ip_list() == []

    def test_line_ip_never_touches_credentials(self):
        assert MassUpdater._line_ip("10.0.0.2:8729|user|p@ss|SSL\n") == '10.0.0.2'

    def test_start_line_still_applies(self, tmp_path):
        path = self._list_file(tmp_path, "10.0.0.1\n10.0.0.2\n")
        updater = MassUpdater(_make_args(ip_list=str(path), start_line=2))
        assert updater._load_ip_list() == ['10.0.0.2\n']


class TestRetryFailed:
    def _state(self, base, run_id, results):
        run_dir = base / run_id
        run_dir.mkdir(parents=True)
        render_state(results, build_summary(results, 1.0), run_id, run_dir / 'state.json')
        return run_dir

    def _list_file(self, tmp_path):
        path = tmp_path / 'list.txt'
        path.write_text("10.0.0.1\n10.0.0.2\n10.0.0.3\n", encoding='utf-8')
        return path

    def test_latest_state_selects_only_failed(self, tmp_path):
        log_base = tmp_path / 'log'
        log_base.mkdir()
        self._state(
            log_base,
            '2026-01-01-10-00-00',
            [
                HostResult(ip='10.0.0.1', port=8728, success=True),
                HostResult(ip='10.0.0.2', port=8728, success=False),
            ],
        )
        latest = self._state(
            log_base,
            '2026-02-01-10-00-00',
            [
                HostResult(ip='10.0.0.1', port=8728, success=False),
                HostResult(ip='10.0.0.3', port=8728, success=True),
            ],
        )
        updater = MassUpdater(
            _make_args(ip_list=str(self._list_file(tmp_path)), retry_failed='latest'),
            log_base=str(log_base),
        )
        assert updater._load_ip_list() == ['10.0.0.1\n']
        assert latest.exists()

    def test_skips_failed_host_confirmed_up_to_date(self, tmp_path):
        log_base = tmp_path / 'log'
        log_base.mkdir()
        self._state(
            log_base,
            '2026-02-01-10-00-00',
            [
                HostResult(
                    ip='10.0.0.1', port=8728, success=False,
                    update_available=False,
                ),
                HostResult(
                    ip='10.0.0.2', port=8728, success=False,
                    update_available=None,
                ),
                HostResult(
                    ip='10.0.0.3', port=8728, success=False,
                    update_available=True,
                ),
            ],
        )
        updater = MassUpdater(
            _make_args(ip_list=str(self._list_file(tmp_path)), retry_failed='latest'),
            log_base=str(log_base),
        )

        assert updater._load_ip_list() == ['10.0.0.2\n', '10.0.0.3\n']

    def test_accepts_explicit_directory(self, tmp_path):
        run_dir = self._state(
            tmp_path,
            '2026-03-01-10-00-00',
            [HostResult(ip='10.0.0.3', port=8728, success=False)],
        )
        updater = MassUpdater(
            _make_args(ip_list=str(self._list_file(tmp_path)), retry_failed=str(run_dir))
        )
        assert updater._load_ip_list() == ['10.0.0.3\n']

    def test_missing_state_raises(self, tmp_path):
        updater = MassUpdater(
            _make_args(ip_list=str(self._list_file(tmp_path)), retry_failed='latest'),
            log_base=str(tmp_path / 'missing'),
        )
        with pytest.raises(RetryStateMissing):
            updater._load_ip_list()

    def test_run_returns_failure_when_state_is_missing(self, tmp_path):
        updater = MassUpdater(
            _make_args(ip_list=str(self._list_file(tmp_path)), retry_failed='latest'),
            log_base=str(tmp_path / 'missing'),
        )
        assert updater.run() is True

    def test_no_failed_hosts_returns_empty_selection(self, tmp_path):
        run_dir = self._state(
            tmp_path,
            '2026-04-01-10-00-00',
            [HostResult(ip='10.0.0.1', port=8728, success=True)],
        )
        updater = MassUpdater(
            _make_args(ip_list=str(self._list_file(tmp_path)), retry_failed=str(run_dir))
        )
        assert updater._load_ip_list() == []

    def test_retried_host_missing_from_list_is_skipped(self, tmp_path):
        run_dir = self._state(
            tmp_path,
            '2026-05-01-10-00-00',
            [
                HostResult(ip='10.0.0.1', port=8728, success=False),
                HostResult(ip='192.0.2.50', port=8728, success=False),
            ],
        )
        updater = MassUpdater(
            _make_args(ip_list=str(self._list_file(tmp_path)), retry_failed=str(run_dir))
        )
        assert updater._load_ip_list() == ['10.0.0.1\n']


class TestReportsWrittenByRun:
    def _populated(self, run_dir, report=True):
        updater = MassUpdater(
            _make_args(report=report), run_dir=run_dir, run_id=run_dir.name
        )
        updater.aggregated_results = [
            HostResult(
                ip='10.0.0.1', port=8728, success=True, status='ok',
                duration_s=1.0, identity='edge-1', model='RB750Gr3',
            )
        ]
        updater._start_time = time.time() - 1
        return updater

    def test_writes_three_files(self, tmp_path):
        run_dir = tmp_path / '2026-06-01-10-00-00'
        run_dir.mkdir()
        updater = self._populated(run_dir)
        updater._write_reports()
        assert (run_dir / 'report.html').is_file()
        assert (run_dir / 'report.csv').is_file()
        assert (run_dir / 'state.json').is_file()

    def test_no_report_flag_writes_nothing(self, tmp_path):
        run_dir = tmp_path / '2026-06-01-10-00-00'
        run_dir.mkdir()
        updater = self._populated(run_dir, report=False)
        updater._write_reports()
        assert list(run_dir.iterdir()) == []

    def test_empty_run_still_writes_reports(self, tmp_path):
        run_dir = tmp_path / '2026-06-01-10-00-00'
        run_dir.mkdir()
        updater = MassUpdater(_make_args(), run_dir=run_dir)
        updater._write_reports()
        assert {path.name for path in run_dir.iterdir()} == {
            'report.html', 'report.csv', 'state.json'
        }

    def test_state_never_contains_credentials(self, tmp_path):
        run_dir = tmp_path / '2026-06-01-10-00-00'
        run_dir.mkdir()
        updater = self._populated(run_dir)
        updater.aggregated_results[0].cloud_backup_key = 'super-secret-key'
        updater._write_reports()
        text = (run_dir / 'state.json').read_text(encoding='utf-8')
        assert 'super-secret-key' not in text
        assert 'password' not in text.lower()
        assert json.loads(text)['summary']['total'] == 1

    def test_finalize_writes_before_summary(self, tmp_path, monkeypatch):
        run_dir = tmp_path / '2026-06-01-10-00-00'
        run_dir.mkdir()
        updater = self._populated(run_dir)
        monkeypatch.setattr(updater, '_print_summary', lambda: False)
        assert updater._finalize() is False
        assert (run_dir / 'report.html').is_file()


class TestRunDirectory:
    def test_creates_run_dir(self, tmp_path):
        created = _create_run_dir('2026-01-01-10-00-00', str(tmp_path))
        assert created.is_dir()
        assert created.name == '2026-01-01-10-00-00'

    def test_suffixes_on_collision(self, tmp_path):
        first = _create_run_dir('2026-01-01-10-00-00', str(tmp_path))
        second = _create_run_dir('2026-01-01-10-00-00', str(tmp_path))
        third = _create_run_dir('2026-01-01-10-00-00', str(tmp_path))
        assert first.name == '2026-01-01-10-00-00'
        assert second.name == '2026-01-01-10-00-00-2'
        assert third.name == '2026-01-01-10-00-00-3'

    def test_logger_writes_inside_run_dir(self, tmp_path):
        run_dir = _create_run_dir('2026-01-01-10-00-00', str(tmp_path))
        logger = _setup_logger(False, False, run_dir)
        try:
            logger.info('hello')
            assert (run_dir / 'mkmassupdate.log').is_file()
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()


class TestNewCliOptions:
    def _args(self, monkeypatch, argv):
        monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass'] + argv)
        return _parse_args()

    def test_inventory_flag(self, monkeypatch):
        assert self._args(monkeypatch, ['--inventory']).inventory is True

    def test_only_option(self, monkeypatch):
        assert self._args(monkeypatch, ['--only', '10.0.0.1,10.0.0.2']).only == '10.0.0.1,10.0.0.2'

    def test_only_rejects_empty_value(self, monkeypatch):
        with pytest.raises(SystemExit):
            self._args(monkeypatch, ['--only', ' , '])

    def test_retry_failed_defaults_to_latest(self, monkeypatch):
        assert self._args(monkeypatch, ['--retry-failed']).retry_failed == 'latest'

    def test_retry_failed_accepts_path(self, monkeypatch):
        args = self._args(monkeypatch, ['--retry-failed', '/tmp/run'])
        assert args.retry_failed == '/tmp/run'

    def test_no_report_flag(self, monkeypatch):
        assert self._args(monkeypatch, ['--no-report']).report is False

    def test_reports_are_on_by_default(self, monkeypatch):
        assert self._args(monkeypatch, []).report is True

    def test_config_supports_new_keys(self, monkeypatch, tmp_path):
        config = tmp_path / 'config.yaml'
        config.write_text(
            "inventory: true\nreport: false\nonly: '10.0.0.1'\nretry_failed: latest\n",
            encoding='utf-8',
        )
        args = self._args(monkeypatch, ['--config', str(config)])
        assert args.inventory is True
        assert args.report is False
        assert args.only == '10.0.0.1'
        assert args.retry_failed == 'latest'
