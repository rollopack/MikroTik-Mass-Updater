"""Secure, per-user persistence for non-secret desktop preferences."""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


STRING_KEYS = {"username", "ip_list", "custom_commands", "only"}
INTEGER_KEYS = {
    "threads",
    "timeout",
    "port",
    "ssl_port",
    "update_check_attempts",
    "start_line",
}
FLOAT_KEYS = {"update_check_delay"}
BOOLEAN_KEYS = {"dry_run", "inventory", "ssl", "report", "debug"}
SETTINGS_KEYS = STRING_KEYS | INTEGER_KEYS | FLOAT_KEYS | BOOLEAN_KEYS


class SettingsError(ValueError):
    """Raised when saved preferences cannot be read or validated."""


def settings_path(
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    *,
    windows: bool | None = None,
) -> Path:
    env = os.environ if environ is None else environ
    home_path = Path.home() if home is None else Path(home)
    on_windows = os.name == "nt" if windows is None else windows
    if on_windows:
        appdata = env.get("APPDATA")
        root = Path(appdata) if appdata else home_path / "AppData" / "Roaming"
        return root / "MikroTik Mass Updater" / "settings.json"

    xdg_config = env.get("XDG_CONFIG_HOME")
    root = Path(xdg_config) if xdg_config and Path(xdg_config).is_absolute() else home_path / ".config"
    return root / "mikrotik-mass-updater" / "settings.json"


def _normalise_value(key: str, value: Any) -> Any:
    if key in STRING_KEYS:
        if not isinstance(value, str):
            raise SettingsError(f"Saved setting '{key}' must be text")
        return value

    if key in INTEGER_KEYS:
        if isinstance(value, str):
            if not value.strip().lstrip("+").isdigit():
                raise SettingsError(f"Saved setting '{key}' must be a positive integer")
        elif isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"Saved setting '{key}' must be a positive integer")
        try:
            result = int(value)
        except (TypeError, ValueError) as error:
            raise SettingsError(f"Saved setting '{key}' must be a positive integer") from error
        if result < 1:
            raise SettingsError(f"Saved setting '{key}' must be a positive integer")
        if key in {"port", "ssl_port"} and result > 65535:
            raise SettingsError(f"Saved setting '{key}' must be between 1 and 65535")
        return result

    if key in FLOAT_KEYS:
        if isinstance(value, bool):
            raise SettingsError(f"Saved setting '{key}' must be a positive number")
        try:
            result = float(value)
        except (TypeError, ValueError) as error:
            raise SettingsError(f"Saved setting '{key}' must be a positive number") from error
        if not math.isfinite(result) or result <= 0:
            raise SettingsError(f"Saved setting '{key}' must be a positive number")
        return result

    if key in BOOLEAN_KEYS:
        if not isinstance(value, bool):
            raise SettingsError(f"Saved setting '{key}' must be true or false")
        return value

    raise SettingsError(f"Unsupported saved setting: {key}")


def _empty_host_filter(value: Any) -> bool:
    return isinstance(value, str) and not any(item.strip() for item in value.split(","))


def load_settings(path: Path | None = None) -> dict[str, Any]:
    source = settings_path() if path is None else Path(path)
    try:
        with source.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise SettingsError(f"Could not read valid JSON settings from {source}: {error}") from error

    if not isinstance(data, dict):
        raise SettingsError(f"Settings in {source} must contain a JSON object")

    return {
        key: _normalise_value(key, data[key])
        for key in SETTINGS_KEYS
        if key in data
        and not (key == "only" and _empty_host_filter(data[key]))
    }


def save_settings(values: Mapping[str, Any], path: Path | None = None) -> Path:
    target = settings_path() if path is None else Path(path)
    payload = {
        key: _normalise_value(key, values[key])
        for key in SETTINGS_KEYS
        if key in values
        and not (key == "only" and _empty_host_filter(values[key]))
    }
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".settings-", suffix=".tmp", dir=target.parent
    )
    temporary_path = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        return target
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise
