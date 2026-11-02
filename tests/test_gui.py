import pytest

pytest.importorskip("tkinter")

from mkgui import build_updater_config


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
