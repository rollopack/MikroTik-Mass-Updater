import queue
import threading

import pytest

pytest.importorskip("tkinter")

from mkgui import MikroTikUpdaterApp, build_updater_config


def _values(**overrides):
    values = {
        'username': 'admin',
        'password': 'secret',
        'ip_list': 'hosts.txt',
        'threads': '8',
        'timeout': '12',
        'port': '8728',
        'ssl_port': '8729',
        'update_check_attempts': '15',
        'update_check_delay': '2.0',
        'start_line': '1',
        'dry_run': False,
        'inventory': False,
        'upgrade_firmware': False,
        'ssl': False,
        'ssl_verify': True,
        'ssl_ca_file': '',
        'ssl_legacy_ciphers': False,
        'cloud_backup': False,
        'cloud_password': '',
        'custom_commands': '',
        'only': '',
        'retry_failed': '',
        'report': True,
        'debug': False,
    }
    values.update(overrides)
    return values


def test_gui_config_maps_form_values_to_updater_contract():
    config = build_updater_config(
        _values(
            dry_run=True,
            inventory=True,
            ssl=True,
            ssl_verify=False,
            ssl_ca_file='routers-ca.pem',
            ssl_legacy_ciphers=True,
            only='10.0.0.1, 10.0.0.2',
            retry_failed='/tmp/previous/state.json',
            custom_commands='commands.yaml',
            cloud_backup=True,
            cloud_password='cloud-secret',
            update_check_attempts='9',
            update_check_delay='1.25',
            start_line='4',
        )
    )

    assert config.username == 'admin'
    assert config.password == 'secret'
    assert config.threads == 8
    assert config.timeout == 12
    assert config.port == 8729
    assert config.ssl is True
    assert config.ssl_verify is False
    assert config.ssl_ca_file == 'routers-ca.pem'
    assert config.ssl_legacy_ciphers is True
    assert config.dry_run is True
    assert config.inventory is True
    assert config.only == '10.0.0.1, 10.0.0.2'
    assert config.retry_failed == '/tmp/previous/state.json'
    assert config.custom_commands == 'commands.yaml'
    assert config.cloud_password == 'cloud-secret'
    assert config.update_check_attempts == 9
    assert config.update_check_delay == 1.25
    assert config.start_line == 4
    assert config.report is True


def test_gui_config_defaults_to_verifying_ssl_certificates():
    config = build_updater_config(_values())

    assert config.ssl_verify is True
    assert config.ssl_ca_file is None


def test_responsive_content_uses_fewer_rows_and_log_lines_when_space_is_tight():
    normal = MikroTikUpdaterApp._content_widget_heights(500, row_height=20, line_height=16)
    compact = MikroTikUpdaterApp._content_widget_heights(120, row_height=20, line_height=16)

    assert normal == (10, 7)
    assert compact == (4, 2)


def test_poll_events_handles_events_on_the_calling_thread():
    app = object.__new__(MikroTikUpdaterApp)
    app.events = queue.Queue()
    app.events.put({"type": "test"})
    app.pending_hosts = []
    app.close_when_done = False
    app.run_thread = None
    handled = []

    class FakeRoot:
        def __init__(self):
            self.scheduled = []

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))

    app.root = FakeRoot()
    app._append_pending_hosts = lambda _limit: None
    app._handle_event = lambda event: handled.append((event, threading.get_ident()))
    main_thread = threading.get_ident()

    app._poll_events()

    assert handled == [({"type": "test"}, main_thread)]
    assert app.root.scheduled == [(100, app._poll_events)]


def test_gui_persists_theme_without_persisting_credentials():
    app = object.__new__(MikroTikUpdaterApp)
    saved_values = {
        "username": "operator",
        "ip_list": "list.txt",
        "threads": "5",
        "timeout": "5",
        "port": "8728",
        "ssl_port": "8729",
        "update_check_attempts": "15",
        "update_check_delay": "2.0",
        "start_line": "1",
        "dry_run": False,
        "inventory": False,
        "ssl": False,
        "ssl_verify": True,
        "ssl_ca_file": "",
        "ssl_legacy_ciphers": False,
        "custom_commands": "",
        "only": "",
        "report": True,
        "debug": False,
        "theme": "dark",
        "password": "api-secret",
        "cloud_password": "cloud-secret",
    }
    app._form_values = lambda: saved_values

    preferences = app._settings_values()

    assert preferences["theme"] == "dark"
    assert "password" not in preferences
    assert "cloud_password" not in preferences


@pytest.mark.parametrize(
    ('host', 'expected'),
    [
        (
            {
                'installed_version': '7.25',
                'latest_version': '7.25.1',
                'update_available': True,
            },
            '7.25 → 7.25.1',
        ),
        (
            {
                'installed_version': '7.25.1',
                'latest_version': '7.25.1',
                'update_available': False,
            },
            '7.25.1',
        ),
        (
            {'routeros_version': '7.25'},
            '7.25',
        ),
    ],
)
def test_routeros_label_shows_available_version_transition(host, expected):
    assert MikroTikUpdaterApp._routeros_label(host) == expected


@pytest.mark.parametrize(
    ('values', 'message'),
    [
        ({'username': ''}, 'username'),
        ({'password': ''}, 'password'),
        ({'threads': '0'}, '(?i)threads'),
        ({'port': '65536'}, '(?i)port'),
        ({'update_check_attempts': '0'}, '(?i)update check attempts'),
        ({'start_line': '-1'}, '(?i)start line'),
        ({'cloud_backup': True, 'cloud_password': ''}, '(?i)Cloud Backup'),
    ],
)
def test_gui_config_rejects_invalid_form_values(values, message):
    with pytest.raises(ValueError, match=message):
        build_updater_config(_values(**values))
