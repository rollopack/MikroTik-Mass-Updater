import pytest
import sys
sys.path.insert(0, '/mnt/dropbox/Documenti/Mikrotik/MikroTik-Mass-Updater')

import logging
logging.disable(logging.CRITICAL)

from mkmassupdate import _sanitize_command_item


def test_sanitize_password_param():
    item = ('/user/add', {'name': 'newuser', 'password': 'secret123'})
    result = _sanitize_command_item(item)
    cmd, params = result
    assert params['password'] == '********'
    assert params['name'] == 'newuser'


def test_sanitize_secret_param():
    item = ('/some/command', {'secret-key': 'abc123'})
    result = _sanitize_command_item(item)
    cmd, params = result
    assert params['secret-key'] == '********'


def test_sanitize_skip_non_sensitive():
    item = ('/system/clock/print', {'test': 'value'})
    result = _sanitize_command_item(item)
    cmd, params = result
    assert params['test'] == 'value'


def test_sanitize_plain_string():
    item = '/system/identity/print'
    result = _sanitize_command_item(item)
    assert result == '/system/identity/print'


def test_sanitize_empty_params():
    item = ('/some/command', {})
    result = _sanitize_command_item(item)
    cmd, params = result
    assert params == {}


def test_sanitize_passphrase_param():
    item = ('/cert/sign', {'passphrase': 's3cr3t'})
    result = _sanitize_command_item(item)
    cmd, params = result
    assert params['passphrase'] == '********'


def test_sanitize_pwd_param():
    item = ('/user/add', {'pwd': '12345'})
    result = _sanitize_command_item(item)
    cmd, params = result
    assert params['pwd'] == '********'
