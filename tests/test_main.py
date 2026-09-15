import pytest

import logging
logging.disable(logging.CRITICAL)

import argparse
from mkmassupdate import MassUpdater


def _make_args(**overrides: bool | str | int | float | None) -> argparse.Namespace:
    defaults: dict[str, bool | str | int | float | None] = {
        'username': 'admin',
        'password': 'test',
        'threads': 5,
        'timeout': 5,
        'ip_list': 'list.txt',
        'port': 8728,
        'update_check_attempts': 15,
        'update_check_delay': 2.0,
        'no_colors': False,
        'dry_run': True,
        'start_line': 1,
        'debug': False,
        'cloud_password': None,
        'upgrade_firmware': False,
        'ssl': False,
        'custom_commands': None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestMassUpdaterRun:
    def test_run_returns_false_on_all_success(self, monkeypatch):
        monkeypatch.setattr(MassUpdater, '_load_ip_list', lambda self: [])
        monkeypatch.setattr(MassUpdater, '_print_summary', lambda self: False)

        args = _make_args()
        updater = MassUpdater(args)
        result = updater.run()
        assert result is False

    def test_run_returns_true_on_some_failures(self, monkeypatch):
        monkeypatch.setattr(MassUpdater, '_load_ip_list', lambda self: [])
        monkeypatch.setattr(MassUpdater, '_print_summary', lambda self: True)

        args = _make_args()
        updater = MassUpdater(args)
        result = updater.run()
        assert result is True

    def test_run_returns_true_on_file_not_found(self, monkeypatch):
        def raise_file_not_found(self):
            raise FileNotFoundError

        monkeypatch.setattr(MassUpdater, '_load_ip_list', raise_file_not_found)

        args = _make_args()
        updater = MassUpdater(args)
        result = updater.run()
        assert result is True
