import pytest
import sys
sys.path.insert(0, '/mnt/dropbox/Documenti/Mikrotik/MikroTik-Mass-Updater')

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
    def test_run_returns_false_on_all_success(self, mocker):
        mocker.patch.object(MassUpdater, '_load_ip_list', return_value=[])
        mocker.patch.object(MassUpdater, '_print_summary', return_value=False)

        args = _make_args()
        updater = MassUpdater(args)
        result = updater.run()
        assert result is False

    def test_run_returns_true_on_some_failures(self, mocker):
        mocker.patch.object(MassUpdater, '_load_ip_list', return_value=[])
        mocker.patch.object(MassUpdater, '_print_summary', return_value=True)

        args = _make_args()
        updater = MassUpdater(args)
        result = updater.run()
        assert result is True

    def test_run_returns_true_on_file_not_found(self, mocker):
        mocker.patch.object(MassUpdater, '_load_ip_list', side_effect=FileNotFoundError)

        args = _make_args()
        updater = MassUpdater(args)
        result = updater.run()
        assert result is True
