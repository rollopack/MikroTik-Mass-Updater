import pytest
import sys
import os
import tempfile
sys.path.insert(0, '/mnt/dropbox/Documenti/Mikrotik/MikroTik-Mass-Updater')

import logging
logging.disable(logging.CRITICAL)

from mkmassupdate import _parse_args


def _write_yaml(content: str) -> str:
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8')
    tmp.write(content)
    tmp.close()
    return tmp.name


class TestConfigFile:

    def test_config_overrides_default(self, monkeypatch):
        config_path = _write_yaml("threads: 10\ntimeout: 30\n")
        try:
            monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass', '--config', config_path])
            args = _parse_args()
            assert args.threads == 10
            assert args.timeout == 30
        finally:
            os.unlink(config_path)

    def test_cli_overrides_config(self, monkeypatch):
        config_path = _write_yaml("threads: 10\n")
        try:
            monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass', '--config', config_path, '--threads', '3'])
            args = _parse_args()
            assert args.threads == 3
        finally:
            os.unlink(config_path)

    def test_config_file_not_found(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass', '--config', '/nonexistent/config.yaml'])
        with pytest.raises(SystemExit):
            _parse_args()

    def test_config_invalid_yaml(self, monkeypatch):
        config_path = _write_yaml(": : invalid yaml\n")
        try:
            monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass', '--config', config_path])
            with pytest.raises(SystemExit):
                _parse_args()
        finally:
            os.unlink(config_path)

    def test_config_partial(self, monkeypatch):
        config_path = _write_yaml("dry_run: true\n")
        try:
            monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass', '--config', config_path])
            args = _parse_args()
            assert args.dry_run is True
            assert args.threads == 5
        finally:
            os.unlink(config_path)

    def test_config_boolean_false(self, monkeypatch):
        config_path = _write_yaml("ssl: false\ndry_run: false\n")
        try:
            monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass', '--config', config_path])
            args = _parse_args()
            assert args.ssl is False
            assert args.dry_run is False
        finally:
            os.unlink(config_path)

    def test_config_types_preserved(self, monkeypatch):
        config_path = _write_yaml("port: 8729\ntimeout: 30\nupdate_check_attempts: 20\nupdate_check_delay: 1.5\n")
        try:
            monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass', '--config', config_path])
            args = _parse_args()
            assert args.port == 8729
            assert args.timeout == 30
            assert args.update_check_attempts == 20
            assert args.update_check_delay == 1.5
        finally:
            os.unlink(config_path)

    def test_config_username_without_cli_u(self, monkeypatch):
        config_path = _write_yaml("username: myadmin\n")
        try:
            monkeypatch.setattr(sys, 'argv', ['prog', '-p', 'pass', '--config', config_path])
            args = _parse_args()
            assert args.username == 'myadmin'
        finally:
            os.unlink(config_path)

    def test_config_missing_username_raises_error(self, monkeypatch):
        config_path = _write_yaml("dry_run: true\n")
        try:
            monkeypatch.setattr(sys, 'argv', ['prog', '-p', 'pass', '--config', config_path])
            with pytest.raises(SystemExit):
                _parse_args()
        finally:
            os.unlink(config_path)

    def test_config_unknown_keys_ignored(self, monkeypatch):
        config_path = _write_yaml("unknown_key: value\nthreads: 7\n")
        try:
            monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass', '--config', config_path])
            args = _parse_args()
            assert args.threads == 7
        finally:
            os.unlink(config_path)

    def test_config_not_a_mapping(self, monkeypatch):
        config_path = _write_yaml("- list\n- of\n- items\n")
        try:
            monkeypatch.setattr(sys, 'argv', ['prog', '-u', 'admin', '-p', 'pass', '--config', config_path])
            with pytest.raises(SystemExit):
                _parse_args()
        finally:
            os.unlink(config_path)
