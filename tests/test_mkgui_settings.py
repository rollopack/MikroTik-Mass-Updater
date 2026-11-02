import json
import os
import stat

import pytest

import mkgui_settings
from mkgui_settings import SettingsError, load_settings, save_settings, settings_path


def test_settings_path_uses_platform_user_config_location(tmp_path):
    assert settings_path(
        environ={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        windows=False,
    ) == tmp_path / "xdg" / "mikrotik-mass-updater" / "settings.json"
    assert settings_path(
        environ={"APPDATA": str(tmp_path / "roaming")},
        windows=True,
    ) == tmp_path / "roaming" / "MikroTik Mass Updater" / "settings.json"
    assert settings_path(environ={"APPDATA": ""}, home=tmp_path, windows=True) == (
        tmp_path / "AppData" / "Roaming" / "MikroTik Mass Updater" / "settings.json"
    )
    assert settings_path(environ={"XDG_CONFIG_HOME": "relative"}, home=tmp_path, windows=False) == (
        tmp_path / ".config" / "mikrotik-mass-updater" / "settings.json"
    )


def test_save_load_filters_credentials_and_one_shot_actions(tmp_path):
    path = tmp_path / "settings.json"
    values = {
        "username": "operator",
        "password": "api-secret",
        "cloud_password": "cloud-secret",
        "retry_failed": "latest",
        "upgrade_firmware": True,
        "cloud_backup": True,
        "only": " , ",
        "threads": "8",
        "timeout": "15",
        "update_check_delay": "1.5",
        "ssl": True,
        "report": False,
    }

    save_settings(values, path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    loaded = load_settings(path)

    assert stored == {
        "username": "operator",
        "threads": 8,
        "timeout": 15,
        "update_check_delay": 1.5,
        "ssl": True,
        "report": False,
    }
    assert loaded == stored
    assert "password" not in stored
    assert "cloud_password" not in stored
    assert "retry_failed" not in stored
    assert "upgrade_firmware" not in stored
    assert "cloud_backup" not in stored
    assert "only" not in stored
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_is_atomic_and_leaves_existing_settings_untouched(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text('{"threads": 3}', encoding="utf-8")

    def fail_dump(*_args, **_kwargs):
        raise RuntimeError("simulated serialization failure")

    monkeypatch.setattr(mkgui_settings.json, "dump", fail_dump)
    with pytest.raises(RuntimeError, match="serialization failure"):
        save_settings({"threads": 9}, path)

    assert path.read_text(encoding="utf-8") == '{"threads": 3}'
    assert list(tmp_path.iterdir()) == [path]


def test_load_rejects_corrupt_json_and_invalid_values(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(SettingsError, match="valid JSON"):
        load_settings(path)

    path.write_text('{"threads": 0}', encoding="utf-8")
    with pytest.raises(SettingsError, match="threads"):
        load_settings(path)


def test_missing_settings_file_returns_empty_mapping(tmp_path):
    assert load_settings(tmp_path / "missing.json") == {}


def test_empty_saved_host_filter_is_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"only": " , "}', encoding="utf-8")
    assert "only" not in load_settings(path)


def test_load_ignores_auth_secrets_and_rejects_non_integer_numeric_fields(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        '{"password": "must-not-load", "cloud_password": "must-not-load", "threads": 4}',
        encoding="utf-8",
    )
    assert load_settings(path) == {"threads": 4}

    path.write_text('{"threads": 4.5}', encoding="utf-8")
    with pytest.raises(SettingsError, match="threads"):
        load_settings(path)
