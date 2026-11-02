import pytest

import logging
logging.disable(logging.CRITICAL)

import argparse
import sys
from mkmassupdate import _positive_int, _port_type, _positive_float, _parse_args


class TestPositiveInt:
    def test_accepts_one(self):
        assert _positive_int('1') == 1

    def test_accepts_large(self):
        assert _positive_int('100') == 100

    def test_rejects_zero(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_int('0')

    def test_rejects_negative(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_int('-5')

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError):
            _positive_int('abc')


class TestPortType:
    def test_accepts_standard_port(self):
        assert _port_type('8728') == 8728

    def test_accepts_ssl_port(self):
        assert _port_type('8729') == 8729

    def test_accepts_min_port(self):
        assert _port_type('1') == 1

    def test_accepts_max_port(self):
        assert _port_type('65535') == 65535

    def test_rejects_zero(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _port_type('0')

    def test_rejects_negative(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _port_type('-1')

    def test_rejects_overflow(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _port_type('65536')

    def test_rejects_large(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _port_type('99999')


class TestPositiveFloat:
    def test_accepts_positive_int_str(self):
        assert _positive_float('2') == 2.0

    def test_accepts_float(self):
        assert _positive_float('2.5') == 2.5

    def test_accepts_small_positive(self):
        assert _positive_float('0.1') == 0.1

    def test_rejects_zero(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_float('0')

    def test_rejects_negative(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_float('-1.5')


class TestSslPort:
    def test_default_ssl_port(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass'])
        assert _parse_args().ssl_port == 8729

    def test_ssl_port_override(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass', '--ssl-port', '8730'])
        assert _parse_args().ssl_port == 8730

    def test_rejects_invalid_ssl_port(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass', '--ssl-port', '99999'])
        with pytest.raises(SystemExit):
            _parse_args()


class TestGuiArgumentParsing:
    def test_gui_can_parse_cli_options_without_credentials(self, tmp_path):
        retry_state = tmp_path / 'state.json'
        args = _parse_args(
            [
                '--ip-list', 'routers.txt', '-t', '12', '--timeout', '20',
                '--port', '8727', '--ssl-port', '8730',
                '--update-check-attempts', '9', '--update-check-delay', '1.25',
                '--start-line', '3', '--dry-run', '--debug', '--no-colors',
                '--cloud-password', 'cloud-secret', '--upgrade-firmware', '--ssl',
                '--custom-commands', 'commands.yaml', '--inventory',
                '--only', '10.0.0.1,10.0.0.2', '--retry-failed', str(retry_state),
                '--no-report',
            ],
            require_username=False,
        )

        assert args.username is None
        assert args.password is None
        assert args.ip_list == 'routers.txt'
        assert args.threads == 12
        assert args.timeout == 20
        assert args.port == 8727
        assert args.ssl_port == 8730
        assert args.update_check_attempts == 9
        assert args.update_check_delay == 1.25
        assert args.start_line == 3
        assert args.dry_run is True
        assert args.debug is True
        assert args.no_colors is True
        assert args.cloud_password == 'cloud-secret'
        assert args.upgrade_firmware is True
        assert args.ssl is True
        assert args.custom_commands == 'commands.yaml'
        assert args.inventory is True
        assert args.only == '10.0.0.1,10.0.0.2'
        assert args.retry_failed == str(retry_state)
        assert args.report is False

    def test_gui_argument_precedence_is_cli_then_config_then_saved_settings(self, tmp_path):
        config_path = tmp_path / 'config.yaml'
        config_path.write_text(
            'threads: 4\ntimeout: 30\nupdate_check_delay: 1.5\n',
            encoding='utf-8',
        )

        args = _parse_args(
            ['--config', str(config_path), '--threads', '7'],
            require_username=False,
            defaults={'threads': 8, 'timeout': 9, 'dry_run': True},
        )

        assert args.threads == 7
        assert args.timeout == 30
        assert args.dry_run is True
        assert args.update_check_delay == 1.5
