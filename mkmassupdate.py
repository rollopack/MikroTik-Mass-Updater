#!/usr/bin/env python3

from __future__ import annotations

####################################################
#  MikroTik Mass Updater
#  Original Written by: Phillip Hutchison
#  Revamped version by: Kevin Byrd
# Copyright (C) 2026 Rolland Gabriel (https://github.com/rollopack)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
####################################################

import threading
import queue
import time
import argparse
import librouteros
import socket
import ssl
import logging
import os
import sys
import getpass
import re
import yaml
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any
from tqdm import tqdm
from librouteros.query import Key

from mkreport import (
    HostResult,
    Summary,
    build_summary,
    failed_ips,
    format_elapsed,
    latest_state_path,
    parse_identity_entry,
    parse_resource_entry,
    parse_routerboard_entry,
    parse_update_entry,
    read_state,
    write_reports,
)
from mkreport import STATE_FILENAME

VERSION = "6.0.0"

BASE_LOG_DIR = "log"
LOG_FILENAME = "mkmassupdate.log"
REPORT_BASE_DIR = BASE_LOG_DIR


log_lock = threading.Lock()


class Colors:
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


class ColoredFormatter(logging.Formatter):
    LOG_COLORS: dict[int, str] = {
        logging.DEBUG: Colors.OKBLUE,
        logging.INFO: Colors.OKGREEN,
        logging.WARNING: Colors.WARNING,
        logging.ERROR: Colors.FAIL,
        logging.CRITICAL: Colors.FAIL + Colors.BOLD,
    }

    CONSOLE_FORMAT: str = "%(message)s"
    CONSOLE_FORMAT_WITH_LEVEL: str = "%(levelname)s: %(message)s"

    def __init__(self, use_colors: bool = True) -> None:
        super().__init__()
        self.use_colors = use_colors
        if self.use_colors:
            self.formatters: dict[int, logging.Formatter] = {
                level: logging.Formatter(
                    f"{color_val}{self.CONSOLE_FORMAT if level == logging.INFO else self.CONSOLE_FORMAT_WITH_LEVEL}{Colors.ENDC}"
                )
                for level, color_val in self.LOG_COLORS.items()
            }
        else:
            self.formatters = {
                level: logging.Formatter(
                     self.CONSOLE_FORMAT if level == logging.INFO else self.CONSOLE_FORMAT_WITH_LEVEL
                )
                 for level in self.LOG_COLORS.keys()
            }

        self.default_formatter = logging.Formatter(self.CONSOLE_FORMAT_WITH_LEVEL)

    def format(self, record: logging.LogRecord) -> str:
        message_content = record.getMessage()
        if not message_content.strip():
            return ""
        formatter = self.formatters.get(record.levelno, self.default_formatter)
        return formatter.format(record)


class TqdmLoggingHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)


class NoEmptyMessagesFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return bool(record.getMessage().strip())


class _EventLogLines(list[str]):
    """List-compatible host log that streams each appended line to the UI."""

    def __init__(self, emit_line: Any) -> None:
        super().__init__()
        self._emit_line = emit_line

    def append(self, line: str) -> None:
        super().append(line)
        self._emit_line(line)


def _new_run_id() -> str:
    return time.strftime('%Y-%m-%d-%H-%M-%S')


def _create_run_dir(run_id: str, base_dir: str = BASE_LOG_DIR) -> Path:
    """Create (or suffix) the per-run output directory."""
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    candidate = base / run_id
    suffix = 2
    while candidate.exists():
        candidate = base / f"{run_id}-{suffix}"
        suffix += 1
    candidate.mkdir()
    return candidate


def _setup_logger(
    use_colors_arg: bool,
    debug_level: bool = False,
    run_dir: Path | None = None,
    *,
    console: bool = True,
) -> logging.Logger:
    logger_instance = logging.getLogger("MKMikroTikUpdater")
    logger_instance.setLevel(logging.DEBUG if debug_level else logging.INFO)
    logger_instance.propagate = False

    if run_dir is None:
        # Legacy flat layout, kept for helpers that only want a console log.
        os.makedirs(BASE_LOG_DIR, exist_ok=True)
        log_path = os.path.join(
            BASE_LOG_DIR, f"mkmassupdate-{time.strftime('%Y-%m-%d-%H-%M-%S')}.log"
        )
    else:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        log_path = str(run_dir / LOG_FILENAME)

    fh = logging.FileHandler(log_path, mode='w', encoding='utf-8')
    fh_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')
    fh.setFormatter(fh_formatter)
    fh.addFilter(NoEmptyMessagesFilter())
    logger_instance.addHandler(fh)

    if console:
        ch = TqdmLoggingHandler()
        ch_formatter = ColoredFormatter(use_colors=use_colors_arg)
        ch.setFormatter(ch_formatter)
        logger_instance.addHandler(ch)

    return logger_instance


logger = logging.getLogger("MKMikroTikUpdater")


def execute_with_retry(
    api: librouteros.Connection,
    command: str | tuple[Any, ...],
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
    retry_delay: int = 5,
    retry_messages: list[str] | None = None,
) -> list[dict[str, Any]] | None:
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")
    last_exception: Exception | None = None
    for attempt in range(max_retries):
        try:
            if params is not None:
                return list(api(command, **params))
            return list(api(command))
        except librouteros.exceptions.TrapError as e:
            msg = getattr(e, 'message', '') or str(e)
            msg_lower = msg.lower()
            is_transient = any(term in msg_lower for term in [
                'connection', 'timeout', 'connect', 'resolve', 'system is busy'
            ])
            if not is_transient:
                raise
            last_exception = e
            if retry_messages is not None:
                retry_messages.append(
                    f"  Retry {attempt + 1} failed: {e}\n"
                )
            else:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (2 ** attempt))
                continue
            raise last_exception
        except (TimeoutError, socket.error, librouteros.exceptions.LibRouterosError) as e:
            last_exception = e
            if retry_messages is not None:
                retry_messages.append(
                    f"  Retry {attempt + 1} failed: {e}\n"
                )
            else:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (2 ** attempt))
                continue
            raise last_exception
    raise RuntimeError("execute_with_retry exhausted retries without returning or raising")


def parse_host_line(
    line: str,
    default_api_port: int,
    default_ssl_port: int = 8729,
) -> tuple[str, int, str | None, str | None, bool] | None:
    stripped_line = line.strip()
    try:
        parts = stripped_line.split('|')
        use_ssl = False
        if parts and parts[-1].strip().upper() == 'SSL':
            use_ssl = True
            parts = parts[:-1]

        ip_port_str = parts[0]
        if not ip_port_str:
            raise ValueError("IP/Port part is empty")

        ip_port_parts = ip_port_str.split(':')
        ip = ip_port_parts[0]
        if not ip:
            raise ValueError("IP address cannot be empty")

        has_custom_port = len(ip_port_parts) > 1
        if has_custom_port:
            port_str = ip_port_parts[1]
        else:
            port_str = str(default_ssl_port) if use_ssl else str(default_api_port)
        port = int(port_str)
        if not (1 <= port <= 65535):
            raise ValueError(f"Port number {port} out of range (1-65535)")

        username = parts[1] if len(parts) > 1 else None
        password = parts[2] if len(parts) > 2 else None

        if len(parts) > 3:
            logger.warning(
                f"IP list entry for {ip} has extra fields after the password; "
                "they are ignored (passwords containing '|' are not supported)."
            )

        return ip, port, username, password, use_ssl
    except ValueError as e:
        logger.warning(f"Skipping malformed line in IP list: '{stripped_line}'. Error: {e}")
        return None
    except IndexError:
        logger.warning(f"Skipping malformed line due to incorrect format: '{stripped_line}'. Check pipe and colon separators.")
        return None


def _connect_to_router(
    host_info: tuple[str, int, str | None, str | None, bool],
    default_username: str,
    default_password: str,
    timeout: int,
    global_ssl: bool = False,
) -> librouteros.Connection:
    IP, port, custom_username, custom_password, use_ssl = host_info
    use_ssl = use_ssl or global_ssl
    username = custom_username or default_username
    password = custom_password or default_password

    connect_kwargs: dict[str, Any] = dict(
        host=IP,
        username=username,
        password=password,
        port=int(port),
        timeout=timeout
    )

    if use_ssl:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        ssl_context.set_ciphers('ALL:@SECLEVEL=0')
        connect_kwargs['ssl_wrapper'] = ssl_context.wrap_socket

    return librouteros.connect(**connect_kwargs)


def _sanitize_command_item(command_item: str | tuple[str, dict[str, Any]]) -> str | tuple[str, dict[str, Any]]:
    if isinstance(command_item, tuple):
        cmd, params = command_item
        if isinstance(params, dict):
            sanitized_params: dict[str, Any] = {}
            for k, v in params.items():
                if re.search(r'\b(pass(?:word|phrase)?|pwd|secret)\b', k.lower()):
                    sanitized_params[k] = '********'
                else:
                    sanitized_params[k] = v
            return (cmd, sanitized_params)
    return command_item


def _execute_router_command(
    api: librouteros.Connection,
    command_item: str | tuple[str, dict[str, Any]],
    entry_lines: list[str],
) -> list[dict[str, Any]] | None:
    try:
        if isinstance(command_item, tuple):
            cmd, params = command_item
            response = execute_with_retry(api, cmd, params, retry_messages=entry_lines)
        else:
            response = execute_with_retry(api, command_item, retry_messages=entry_lines)
        return response
    except Exception as e:
        sanitized_item = _sanitize_command_item(command_item)
        entry_lines.append(f"  Error executing command {sanitized_item}: {type(e).__name__}: {e}\n")
    return None


def _process_identity(
    response: list[dict[str, Any]], entry_lines: list[str]
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if response:
        for res in response:
            data = parse_identity_entry(res)
            entry_lines.append(f"  Identity: {data['identity'] or 'N/A'}\n")
    return data


def _process_routerboard(
    response: list[dict[str, Any]], entry_lines: list[str]
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if response:
        for res in response:
            data = parse_routerboard_entry(res)
            entry_lines.append(f"  Model: {data['model'] or 'N/A'}\n")
    return data


def _process_resource(
    response: list[dict[str, Any]], entry_lines: list[str]
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if response:
        for res in response:
            data = parse_resource_entry(res)
            entry_lines.append(f"  Version: {data['routeros_version'] or 'N/A'}\n")
    return data


@dataclass
class UpdateOutcome:
    ok: bool = False
    reboot_required: bool = False
    installed_version: str | None = None
    latest_version: str | None = None
    available: bool | None = None
    status: str | None = None


def _check_and_process_updates(
    api: librouteros.Connection,
    entry_lines: list[str],
    dry_run: bool,
    check_attempts: int,
    check_delay: float,
    allow_install: bool = True,
) -> UpdateOutcome:
    outcome = UpdateOutcome()
    entry_lines.append("  Checking for updates...\n")
    response = _execute_router_command(api, '/system/package/update/check-for-updates', entry_lines)
    if response is None:
        return outcome

    check_complete = False
    for _ in range(check_attempts):
        time.sleep(check_delay)
        status_response = _execute_router_command(api, '/system/package/update/print', entry_lines)
        if status_response:
            status = status_response[0].get('status', '').lower()
            if 'checking' not in status:
                entry_lines.append(f"  Status: {status}\n")
                check_complete = True
                break
        else:
            return outcome

    if not check_complete:
        entry_lines.append("  Timeout waiting for update check to complete.\n")
        return outcome

    status_response = _execute_router_command(api, '/system/package/update/print', entry_lines)
    if not status_response:
        return outcome

    outcome.ok = True
    for res in status_response:
        parsed = parse_update_entry(res)
        installed_version = parsed['installed_version'] or ''
        latest_version = parsed['latest_version'] or ''
        outcome.installed_version = parsed['installed_version']
        outcome.latest_version = parsed['latest_version']
        outcome.available = parsed['update_available']
        outcome.status = parsed['status']

        if outcome.available:
            entry_lines.append(f"  Updates available: {installed_version} -> {latest_version}\n")
            if not allow_install:
                entry_lines.append(
                    "  Update installation skipped because the firmware check failed.\n"
                )
                return outcome
            if not dry_run:
                time.sleep(2)
                try:
                    update_package_path = api.path('system', 'package', 'update')
                    execute_with_retry(
                        update_package_path,
                        'install',
                        max_retries=2,
                        retry_messages=entry_lines,
                    )
                    entry_lines.append("  Updates installed. Rebooting...\n")
                    outcome.reboot_required = True
                    return outcome
                except Exception as e:
                    entry_lines.append(f"  Error installing updates: {type(e).__name__}: {e}\n")
                    outcome.ok = False
                    return outcome
            else:
                entry_lines.append("  Dry-run: Skipping installation of updates.\n")

    return outcome


@dataclass
class FirmwareOutcome:
    status: bool | None = None
    current: str | None = None
    upgrade: str | None = None


def _perform_firmware_upgrade(
    api: librouteros.Connection,
    entry_lines: list[str],
    dry_run: bool = False,
) -> FirmwareOutcome:
    routerboard_info = _execute_router_command(api, '/system/routerboard/print', entry_lines)
    if not routerboard_info:
        entry_lines.append("  Firmware upgrade: Failed to retrieve routerboard information. Aborting.\n")
        return FirmwareOutcome(False)

    info = routerboard_info[0]
    current_firmware = info.get('current-firmware')
    upgrade_firmware = info.get('upgrade-firmware')
    outcome = FirmwareOutcome(None, current_firmware, upgrade_firmware)

    if not current_firmware or not upgrade_firmware:
        entry_lines.append("  Firmware upgrade: Could not determine firmware versions. Aborting.\n")
        outcome.status = False
        return outcome

    if current_firmware == upgrade_firmware:
        entry_lines.append(f"  Firmware is up to date (current: {current_firmware}). No upgrade needed.\n")
        outcome.status = None
        return outcome

    entry_lines.append(f"  Firmware upgrade available: {current_firmware} -> {upgrade_firmware}\n")
    if dry_run:
        entry_lines.append("  Firmware upgrade: Dry-run — skipping upgrade command.\n")
        outcome.status = None
        return outcome
    upgrade_response = _execute_router_command(api, '/system/routerboard/upgrade', entry_lines)
    if upgrade_response is None:
        entry_lines.append("  Firmware upgrade: Failed.\n")
        outcome.status = False
        return outcome
    entry_lines.append("  Firmware upgrade: Upgrade command sent.\n")
    outcome.status = True
    return outcome


@dataclass
class CloudBackupOutcome:
    success: bool
    secret_key: str | None = None
    replaced_existing_slot: bool = False
    verified: bool | None = None

    def __bool__(self) -> bool:
        return self.success


def _perform_cloud_backup(
    api: librouteros.Connection,
    cloud_password: str,
    entry_lines: list[str],
    dry_run: bool = False,
) -> CloudBackupOutcome:
    if dry_run:
        entry_lines.append("  Cloud backup: Dry-run — would create and upload backup.\n")
        return CloudBackupOutcome(True)

    # Let slow cloud connections stabilize before querying existing backups.
    time.sleep(3)
    existing_backups = _execute_router_command(api, '/system/backup/cloud/print', entry_lines)
    if existing_backups is None:
        entry_lines.append("  Cloud backup: Failed to retrieve list of existing backups. Aborting.\n")
        return CloudBackupOutcome(False)

    upload_params: dict[str, str] = {
        'action': 'create-and-upload',
        'password': cloud_password
    }
    replaced_existing_slot = False
    initial_upload_lines: list[str] = []
    response_upload = _execute_router_command(
        api,
        ('/system/backup/cloud/upload-file', upload_params),
        initial_upload_lines,
    )
    if response_upload is None and any(
        'all slots used' in line.lower()
        for line in initial_upload_lines
    ):
        # RouterOS replaces a Cloud backup by its existing file name.
        replace_name = next(
            (
                backup.get('name')
                for backup in existing_backups
                if isinstance(backup.get('name'), str) and backup['name']
            ),
            None,
        )
        if replace_name is None:
            entry_lines.extend(initial_upload_lines)
            entry_lines.append(
                "  Cloud backup: All slots used, but cloud/print returned no backup name.\n"
            )
            return CloudBackupOutcome(False)

        replace_params = dict(upload_params, replace=replace_name)
        replacement_lines: list[str] = []
        replaced_existing_slot = True
        response_upload = _execute_router_command(
            api,
            ('/system/backup/cloud/upload-file', replace_params),
            replacement_lines,
        )
        if response_upload is None:
            entry_lines.extend(initial_upload_lines)
            entry_lines.append(
                f"  Cloud backup: All slots used; replacing backup {replace_name}.\n"
            )
            entry_lines.extend(replacement_lines)
    elif response_upload is None:
        entry_lines.extend(initial_upload_lines)
    if response_upload is None:
        entry_lines.append("  Cloud backup: Failed to create and upload new backup.\n")
        return CloudBackupOutcome(False)

    entry_lines.append("  Cloud backup: Successfully created and uploaded new backup.\n")
    time.sleep(2)
    verification_lines: list[str] = []
    latest_backups = _execute_router_command(
        api, '/system/backup/cloud/print', verification_lines
    )

    if not latest_backups:
        slots_full = any("all slots used" in line.lower() for line in verification_lines)
        if slots_full:
            entry_lines.append(
                "  Cloud backup: Upload was accepted, but RouterOS reported full slots "
                "during verification. Existing backups were left untouched.\n"
            )
        else:
            entry_lines.append(
                "  Cloud backup: Upload was accepted, but verification failed. "
                "Existing backups were left untouched.\n"
            )
        # RouterOS may accept the upload and then fail to list the cloud slot.
        # Do not report a failed backup or delete any existing backup in that case.
        return CloudBackupOutcome(
            True, replaced_existing_slot=replaced_existing_slot, verified=False
        )

    latest_backup = latest_backups[0]
    new_backup_id = latest_backup.get('.id')
    secret_key = latest_backup.get('secret-download-key')
    if secret_key:
        entry_lines.append(f"  Cloud backup: Secret Download Key: {secret_key}\n")
    else:
        entry_lines.append("  Cloud backup: Could not find secret-download-key for the latest backup.\n")
    old_backup_ids = [] if replaced_existing_slot else [
        backup['.id'] for backup in existing_backups
        if '.id' in backup and backup['.id'] != new_backup_id
    ]
    for backup_id in old_backup_ids:
        remove_params = {'number': backup_id}
        response_remove = _execute_router_command(
            api,
            ('/system/backup/cloud/remove-file', remove_params),
            entry_lines,
        )
        if response_remove is None:
            entry_lines.append(
                f"  Cloud backup: Could not remove old backup {backup_id}; keeping it.\n"
            )

    return CloudBackupOutcome(
        True,
        secret_key=secret_key,
        replaced_existing_slot=replaced_existing_slot,
        verified=True,
    )


def _reboot_router(api: librouteros.Connection, entry_lines: list[str]) -> None:
    reboot_script_name = "mkmassupdate_reboot"
    try:
        name_key = Key('name')
        scripts = list(api.path('/system', 'script').select(name_key).where(name_key == reboot_script_name))
        if scripts is None:
            entry_lines.append(f"  Failed to check for existing script '{reboot_script_name}'. Aborting reboot.\n")
            return

        if not scripts:
            add_script_params: dict[str, str] = {
                'name': reboot_script_name,
                'source': '/system reboot',
                'policy': 'reboot'
            }
            add_response = _execute_router_command(api, ('/system/script/add', add_script_params), entry_lines)
            if add_response is None:
                entry_lines.append("  Failed to create reboot script. Aborting reboot.\n")
                return
            entry_lines.append("  Reboot script created successfully.\n")

        entry_lines.append("  Executing reboot script...\n")
        script_path = api.path('/system', 'script')
        tuple(script_path('run', **{'number': reboot_script_name}))
        time.sleep(1)

    except (socket.error, TimeoutError, ConnectionResetError):
        entry_lines.append("  Router is rebooting as expected. Disconnected.\n")
    except Exception as e:
        entry_lines.append(f"  An unexpected error occurred during the reboot process: {type(e).__name__}: {e}\n")


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError(f"Value must be >= 1, got {n}")
    return n


def _port_type(value: str) -> int:
    n = int(value)
    if not (1 <= n <= 65535):
        raise argparse.ArgumentTypeError(f"Port must be between 1 and 65535, got {n}")
    return n


def _positive_float(value: str) -> float:
    n = float(value)
    if n <= 0:
        raise argparse.ArgumentTypeError(f"Value must be positive, got {n}")
    return n


def _format_elapsed_time(seconds: float) -> str:
    return format_elapsed(seconds)


class RetryStateMissing(RuntimeError):
    """Raised when --retry-failed cannot find a previous run state."""


@dataclass
class UpdaterConfig:
    """Runtime options shared by the CLI and the worker threads."""

    username: str = ""
    password: str = ""
    threads: int = 5
    timeout: int = 5
    ip_list: str = "list.txt"
    port: int = 8728
    ssl_port: int = 8729
    update_check_attempts: int = 15
    update_check_delay: float = 2.0
    dry_run: bool = False
    start_line: int = 1
    cloud_password: str | None = None
    upgrade_firmware: bool = False
    ssl: bool = False
    custom_commands: str | None = None
    inventory: bool = False
    only: str | None = None
    retry_failed: str | None = None
    report: bool = True

    @classmethod
    def from_args(cls, args: Any) -> UpdaterConfig:
        values: dict[str, Any] = {}
        for config_field in fields(cls):
            value = getattr(args, config_field.name, None)
            if value is not None:
                values[config_field.name] = value
        return cls(**values)


class MassUpdater:
    def __init__(
        self,
        args: argparse.Namespace | UpdaterConfig,
        run_dir: Path | None = None,
        log_base: str = BASE_LOG_DIR,
        run_id: str | None = None,
        event_queue: queue.Queue[dict[str, Any]] | None = None,
    ) -> None:
        self.args = args
        self.config = args if isinstance(args, UpdaterConfig) else UpdaterConfig.from_args(args)
        self.q: queue.Queue[tuple[str, int, str | None, str | None, bool]] = queue.Queue()
        self._queue_lock = threading.Lock()
        self.threads: list[threading.Thread] = []
        self.stop_event: threading.Event = threading.Event()
        self.aggregated_results: list[HostResult] = []
        self._start_time: float = 0.0
        self._processed_count: int = 0
        self._success_count: int = 0
        self.run_dir = Path(run_dir) if run_dir is not None else None
        self.log_base = Path(log_base)
        self.run_id = run_id or (self.run_dir.name if self.run_dir else _new_run_id())
        self._report_paths: dict[str, Path] = {}
        self.event_queue = event_queue
        self._total_hosts = 0

    def _emit_event(self, event_type: str, **payload: Any) -> None:
        if self.event_queue is not None:
            self.event_queue.put({"type": event_type, **payload})

    @staticmethod
    def _redact_log(text: str) -> str:
        return re.sub(
            r"(Secret Download Key:\s*)\S+",
            r"\1[redacted]",
            text,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _host_event_data(result: HostResult) -> dict[str, Any]:
        return {
            field.name: getattr(result, field.name)
            for field in fields(HostResult)
            if field.name not in {"cloud_backup_key", "log_lines"}
        }

    def request_stop(self) -> None:
        """Stop after in-flight hosts finish and discard hosts not yet started."""
        with self._queue_lock:
            self.stop_event.set()
            while True:
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break
                else:
                    self.q.task_done()

    def _load_custom_commands(self) -> list:
        custom_commands: list = []
        if not self.config.custom_commands:
            return custom_commands

        try:
            with open(self.config.custom_commands, 'r', encoding='utf-8') as f:
                loaded_commands = yaml.safe_load(f)
        except FileNotFoundError:
            logger.error(f"Custom commands file not found: {self.config.custom_commands}")
            return custom_commands
        except Exception as e:
            logger.error(f"Error parsing custom commands file: {e}")
            return custom_commands

        if loaded_commands is None:
            return custom_commands
        if not isinstance(loaded_commands, list):
            logger.error(
                f"Custom commands file must contain a list, got {type(loaded_commands).__name__}"
            )
            return custom_commands

        for item in loaded_commands:
            if isinstance(item, str):
                custom_commands.append(item)
            elif isinstance(item, dict) and isinstance(item.get('command'), str):
                if 'params' in item:
                    custom_commands.append((item['command'], item['params']))
                else:
                    custom_commands.append(item['command'])
            else:
                logger.warning(f"Skipping malformed custom command entry: {item!r}")

        logger.info(f"Loaded {len(custom_commands)} custom commands from {self.config.custom_commands}")
        return custom_commands

    def _load_ip_list(self) -> list[str]:
        with open(self.config.ip_list, 'r', encoding='utf-8') as f:
            lines = [
                line for i, line in enumerate(f, 1)
                if i >= self.config.start_line and line.strip() and not line.strip().startswith('#')
            ]
        return self._apply_host_filters(lines)

    @staticmethod
    def _line_ip(line: str) -> str:
        """Extract the host part of an IP list line without touching credentials."""
        return line.split('|', 1)[0].split(':', 1)[0].strip()

    @staticmethod
    def _parse_only(value: str | None) -> set[str] | None:
        if not value:
            return None
        hosts = {item.strip() for item in value.split(',') if item.strip()}
        return hosts or None

    def _retry_ips(self) -> set[str] | None:
        target = self.config.retry_failed
        if not target:
            return None

        if target == 'latest':
            state_path = latest_state_path(self.log_base)
            if state_path is None:
                raise RetryStateMissing(
                    f"--retry-failed: no run state found under {self.log_base}/"
                )
        else:
            candidate = Path(target)
            state_path = candidate / STATE_FILENAME if candidate.is_dir() else candidate
            if not state_path.is_file():
                raise RetryStateMissing(f"--retry-failed: state file not found: {state_path}")

        hosts = failed_ips(read_state(state_path))
        if not hosts:
            logger.info(f"--retry-failed: no failed hosts in {state_path}")
        return hosts

    def _apply_host_filters(self, lines: list[str]) -> list[str]:
        only = self._parse_only(self.config.only)
        retry = self._retry_ips()
        if only is None and retry is None:
            return lines

        selected = []
        for line in lines:
            ip = self._line_ip(line)
            if only is not None and ip not in only:
                continue
            if retry is not None and ip not in retry:
                continue
            selected.append(line)

        if only is not None:
            found = {self._line_ip(line) for line in selected}
            missing = only - found
            if missing:
                logger.warning(
                    f"--only: hosts not found in {self.config.ip_list}: "
                    f"{', '.join(sorted(missing))}"
                )
        logger.info(f"Host selection: {len(selected)} of {len(lines)} entries selected.")
        return selected

    def _run_commands_on_router(
        self,
        api: librouteros.Connection,
        custom_commands: list,
        entry_lines: list[str],
        result: HostResult,
    ) -> bool:
        default_commands_map: dict[str, Any] = {
            '/system/identity/print': _process_identity,
            '/system/routerboard/print': _process_routerboard,
            '/system/resource/print': _process_resource,
        }

        if self.config.inventory:
            all_commands_to_process: list = list(default_commands_map)
            if custom_commands:
                entry_lines.append("  Inventory mode: custom commands skipped.\n")
        else:
            all_commands_to_process = list(default_commands_map) + custom_commands

        command_execution_successful = True
        for command_item in all_commands_to_process:
            command_path = command_item[0] if isinstance(command_item, tuple) else command_item
            response = _execute_router_command(api, command_item, entry_lines)
            if response is None:
                command_execution_successful = False
                continue

            if command_path in default_commands_map:
                parsed = default_commands_map[command_path](response, entry_lines)
                result.apply(parsed)
            else:
                entry_lines.append(f"  Response for {command_path}:\n")
                for res_item in response:
                    entry_lines.append(f"    {res_item}\n")

        return command_execution_successful

    def _process_host(
        self,
        host_info: tuple[str, int, str | None, str | None, bool],
        custom_commands: list,
    ) -> HostResult:
        cfg = self.config
        ip, port, _, _, host_ssl = host_info
        result = HostResult(ip=ip, port=port, ssl=cfg.ssl or host_ssl)
        if self.event_queue is not None:
            worker_name = threading.current_thread().name
            result.log_lines = _EventLogLines(
                lambda line: self._emit_event(
                    "log_line",
                    ip=ip,
                    worker=worker_name,
                    message=self._redact_log(line.rstrip("\n")),
                )
            )
        entry_lines = result.log_lines
        entry_lines.append(f"\nHost: {ip}\n")
        started = time.time()
        api: librouteros.Connection | None = None

        try:
            api = _connect_to_router(host_info, cfg.username, cfg.password, cfg.timeout, cfg.ssl)

            commands_ok = self._run_commands_on_router(api, custom_commands, entry_lines, result)
            if not commands_ok:
                result.error = "command execution failed"
                return result

            if cfg.inventory:
                result.success = True
                return result

            success = True
            if cfg.cloud_password:
                backup = _perform_cloud_backup(api, cfg.cloud_password, entry_lines, cfg.dry_run)
                result.backup_ok = None if cfg.dry_run and backup else bool(backup)
                result.backup_verified = backup.verified
                result.cloud_backup_key = backup.secret_key
                if not backup:
                    entry_lines.append("  Warning: Cloud backup failed. Proceeding with updates regardless.\n")

            firmware_upgraded = False
            if cfg.upgrade_firmware:
                firmware = _perform_firmware_upgrade(api, entry_lines, cfg.dry_run)
                result.firmware_current = firmware.current or result.firmware_current
                result.firmware_upgrade = firmware.upgrade or result.firmware_upgrade
                if firmware.status is False:
                    success = False
                    result.error = "firmware upgrade failed"
                elif firmware.status is True:
                    firmware_upgraded = True

            update = _check_and_process_updates(
                api, entry_lines, cfg.dry_run,
                cfg.update_check_attempts, cfg.update_check_delay,
                allow_install=success,
            )
            result.installed_version = update.installed_version
            result.latest_version = update.latest_version
            result.update_available = update.available
            if success and not update.reboot_required and firmware_upgraded:
                _reboot_router(api, entry_lines)

            result.success = success
            return result

        except librouteros.exceptions.TrapError as e:
            msg = getattr(e, 'message', '') or str(e)
            result.error = msg
            entry_lines.append(f"  Error: {msg}\n")
            result.success = False
            return result
        except TimeoutError:
            result.error = f"Connection timed out ({ip}:{port})"
            entry_lines.append(f"  Error: Connection timed out ({ip}:{port})\n")
            result.success = False
            return result
        except socket.error as e:
            reason = e.strerror or e
            result.error = f"Connection failed ({ip}:{port}) - {reason}"
            entry_lines.append(f"  Error: Connection failed ({ip}:{port}) - {reason}\n")
            result.success = False
            return result
        except Exception as e:
            if ip:
                entry_lines.append(f"  Unexpected error processing host {ip}: {type(e).__name__}: {e}\n")
            else:
                entry_lines.append(f"  Unexpected error: {type(e).__name__}: {e}\n")
            result.error = f"{type(e).__name__}: {e}"
            result.success = False
            return result
        finally:
            result.duration_s = time.time() - started
            result.status = "ok" if result.success else "failed"
            if api:
                try:
                    api.close()
                except Exception:
                    pass

    def _worker(
        self,
        pbar: tqdm[Any],
        custom_commands: list,
    ) -> None:
        while not self.stop_event.is_set():
            try:
                host_info = self.q.get(timeout=1)
            except queue.Empty:
                if self.stop_event.is_set():
                    logger.debug(f"Worker {threading.current_thread().name} exiting due to stop_event.")
                return

            if self.stop_event.is_set():
                self.q.task_done()
                return

            worker_name = threading.current_thread().name
            self._emit_event("host_started", ip=host_info[0], worker=worker_name)
            result = self._process_host(host_info, custom_commands)
            entry_lines = result.log_lines
            if not entry_lines:
                entry_lines = [
                    f"\nHost: {result.ip}\n  No operations performed or error before logging started.\n"
                ]

            final_entry_text = "".join(entry_lines).strip()

            with log_lock:
                if final_entry_text:
                    logger.info("─" * 50)
                if result.success:
                    logger.info(final_entry_text)
                else:
                    logger.error(final_entry_text)

                self.aggregated_results.append(result)
                self._processed_count += 1
                if result.success:
                    self._success_count += 1
                fail_count = self._processed_count - self._success_count
                pbar.set_postfix(ok=self._success_count, fail=fail_count)
                pbar.update(1)
                self._emit_event(
                    "host_result", worker=worker_name, host=self._host_event_data(result)
                )
                self._emit_event(
                    "progress",
                    processed=self._processed_count,
                    total=self._total_hosts,
                    succeeded=self._success_count,
                    failed=fail_count,
                )
            self.q.task_done()

    def _start_workers(
        self,
        thread_count: int,
        pbar: tqdm[Any],
        custom_commands: list,
    ) -> None:
        for i in range(thread_count):
            t = threading.Thread(
                target=self._worker,
                args=(pbar, custom_commands),
                name=f"Worker-{i + 1}"
            )
            self.threads.append(t)
            t.start()

    def _populate_queue(self, lines: list[str]) -> None:
        for line_content in lines:
            with self._queue_lock:
                if self.stop_event.is_set():
                    logger.warning("Interruption detected, stopping queue population.")
                    break
                host_info = parse_host_line(line_content, self.config.port, self.config.ssl_port)
                if host_info:
                    self.q.put(host_info)

    def _wait_for_completion(self) -> None:
        self.q.join()

    def _handle_interrupt(self) -> None:
        logger.warning("\nInterrupted by user. Shutting down gracefully...")
        self.stop_event.set()

    def _cleanup_after_interrupt(self) -> None:
        if self.stop_event.is_set():
            logger.warning("Clearing queue due to interruption...")
            self.request_stop()

    def _join_threads(self) -> None:
        for t in self.threads:
            t.join()

    def _summary(self) -> Summary:
        return build_summary(
            self.aggregated_results,
            time.time() - self._start_time,
            dry_run=self.config.dry_run,
            inventory=self.config.inventory,
            script_version=VERSION,
        )

    def _write_reports(self) -> None:
        if not self.config.report or self.run_dir is None:
            return
        try:
            self._report_paths = write_reports(
                self.run_dir, self.aggregated_results, self._summary(), self.run_id
            )
        except OSError as e:
            logger.error(f"Could not write reports to {self.run_dir}: {e}")

    def _finalize(self) -> bool:
        self._write_reports()
        return self._print_summary()

    def _print_summary(self) -> bool:
        summary = self._summary()
        failed_ips_list = [res.ip for res in self.aggregated_results if not res.success]

        title = "JOB SUMMARY"
        if self.config.dry_run:
            title = "JOB SUMMARY (DRY RUN)"
        elif self.config.inventory:
            title = "JOB SUMMARY (INVENTORY)"

        summary_lines = [
            f"\n\n========================================",
            f"             {title}                ",
            f"========================================",
            f" Total hosts processed : {summary.total}",
            f" Successful operations : {summary.ok}",
            f" Failed operations     : {summary.failed}",
            f" Elapsed time          : {summary.elapsed}",
            f"========================================",
        ]

        logger.info("\n".join(summary_lines))

        if summary.failed > 0:
            logger.info(" Failed IPs:")
            for specific_ip in failed_ips_list:
                if specific_ip != "Unknown (worker exited early)":
                    logger.error(f"  [FAIL] - {specific_ip}")
            logger.info("========================================")
        if self._report_paths:
            logger.info(" Reports:")
            logger.info(f"  HTML  : {self._report_paths['html']}")
            logger.info(f"  CSV   : {self._report_paths['csv']}")
            logger.info(f"  State : {self._report_paths['state']}")
        logger.info("-- Job finished --")
        logging.shutdown()
        return summary.failed > 0

    def run(self) -> bool:
        custom_commands = self._load_custom_commands()
        pbar: tqdm[Any] | None = None
        self._start_time = time.time()

        should_finalize = True
        run_error: str | None = None
        try:
            logger.info("-- Starting job --")

            try:
                lines = self._load_ip_list()
            except FileNotFoundError:
                run_error = f"IP list file not found: {self.config.ip_list}"
                logger.error(run_error)
                should_finalize = False
            except RetryStateMissing as e:
                run_error = str(e)
                logger.error(run_error)
                should_finalize = False
            else:
                total_hosts = len(lines)
                self._total_hosts = total_hosts
                display_hosts = []
                for line in lines:
                    parsed = parse_host_line(line, self.config.port, self.config.ssl_port)
                    if parsed:
                        display_hosts.append({"ip": parsed[0], "port": parsed[1]})
                self._emit_event("hosts_loaded", hosts=display_hosts, total=total_hosts)
                desc = "[DRY RUN] Processing hosts" if self.config.dry_run else "Processing hosts"
                pbar = tqdm(total=total_hosts, desc=desc, unit="host")
                pbar.set_postfix(ok=0, fail=0)

                self._start_workers(self.config.threads, pbar, custom_commands)
                self._populate_queue(lines)
                self._wait_for_completion()

        except KeyboardInterrupt:
            self._handle_interrupt()
        finally:
            if pbar:
                pbar.close()
            self._cleanup_after_interrupt()
            self._join_threads()
        has_failures = self._finalize() if should_finalize else True
        summary = self._summary()
        self._emit_event(
            "completed",
            failed=has_failures,
            cancelled=self.stop_event.is_set(),
            summary={
                "total": summary.total,
                "ok": summary.ok,
                "failed": summary.failed,
                "elapsed_s": summary.elapsed_s,
                "elapsed": summary.elapsed,
            },
            report_paths={key: str(value) for key, value in self._report_paths.items()},
            error=run_error,
        )
        return has_failures


def _apply_config_file(
    parser: argparse.ArgumentParser,
    argv: list[str] | None = None,
) -> None:
    known, _ = parser.parse_known_args(argv)
    if not known.config:
        return
    try:
        with open(known.config, encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        parser.error(f"Config file not found: {known.config}")
    except yaml.YAMLError as e:
        parser.error(f"Invalid YAML in config file: {e}")

    if not isinstance(cfg, dict):
        parser.error(f"Config file must contain a top-level mapping, got {type(cfg).__name__}")

    actions = {
        action.dest: action
        for action in parser._actions
        if hasattr(action, 'dest') and action.dest != 'help'
    }
    filtered: dict[str, Any] = {}
    for key, value in cfg.items():
        action = actions.get(key)
        if action is None:
            continue
        if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            if not isinstance(value, bool):
                parser.error(f"Config value '{key}' must be boolean")
            filtered[key] = value
            continue
        if action.type is None and not isinstance(value, str):
            parser.error(f"Config value '{key}' must be a string")
        if action.type is not None:
            try:
                action.type(str(value))
            except (TypeError, ValueError, argparse.ArgumentTypeError) as e:
                parser.error(f"Invalid config value for '{key}': {e}")
        filtered[key] = value
    if filtered:
        parser.set_defaults(**filtered)


def _parse_args(
    argv: list[str] | None = None,
    *,
    require_username: bool = True,
    defaults: dict[str, Any] | None = None,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MikroTik Mass Updater")
    parser.add_argument("-u", "--username", help="API username")
    parser.add_argument("-p", "--password", help="API password. If not provided, it will be asked for securely.")
    parser.add_argument("-t", "--threads", type=_positive_int, default=5, help="Number of threads to use (min: 1)")
    parser.add_argument("--timeout", type=_positive_int, default=5, help="Connection timeout in seconds (min: 1)")
    parser.add_argument("--ip-list", default='list.txt', help="Path to the IP list file.")
    parser.add_argument("--port", type=_port_type, default=8728, help="Default API port (1-65535).")
    parser.add_argument("--ssl-port", type=_port_type, default=8729, help="Default API-SSL port used for hosts marked with |SSL when no port is given (1-65535).")
    parser.add_argument("--update-check-attempts", type=_positive_int, default=15, help="Number of attempts to check update status (min: 1).")
    parser.add_argument("--update-check-delay", type=_positive_float, default=2.0, help="Delay in seconds between update status checks (must be positive).")
    parser.add_argument("--no-colors", action="store_true", help="Disable colored output")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry run mode")
    parser.add_argument("--start-line", type=_positive_int, default=1, help="Start from this line number (min: 1)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging level.")
    parser.add_argument("--cloud-password", help="Password for cloud backup")
    parser.add_argument("--upgrade-firmware", action="store_true", help="Perform firmware upgrade")
    parser.add_argument("--ssl", action="store_true", help="Enable SSL for all connections")
    parser.add_argument("--custom-commands", help="Path to a YAML file with custom commands.")
    parser.add_argument("--inventory", action="store_true", help="Read-only inventory: collect identity, model, firmware and versions without changing devices.")
    parser.add_argument("--only", help="Comma-separated list of hosts to process; the other entries in the IP list are ignored.")
    parser.add_argument("--retry-failed", nargs="?", const="latest", default=None, help="Re-run failed hosts unless a successful check confirmed RouterOS is already current (default: latest state in log/).")
    parser.add_argument("--no-report", dest="report", action="store_false", help="Do not write HTML/CSV/state reports.")
    parser.add_argument("--config", help="Path to a YAML configuration file. CLI arguments override config file values.")
    parser.add_argument("--version", action="version", version=VERSION)

    if defaults:
        parser.set_defaults(**defaults)
    _apply_config_file(parser, argv)
    args = parser.parse_args(argv)

    if require_username and not args.username:
        parser.error("the following arguments are required: -u/--username")

    if args.only is not None and not any(item.strip() for item in args.only.split(',')):
        parser.error("--only requires at least one host")

    return args


def main() -> None:
    try:
        args = _parse_args()

        if not args.password:
            args.password = getpass.getpass(f"Enter password for user '{args.username}': ")

        if args.ssl and args.port == 8728:
            args.port = args.ssl_port

        run_id = _new_run_id()
        run_dir = _create_run_dir(run_id, BASE_LOG_DIR)
        _setup_logger(not args.no_colors, args.debug, run_dir)

        updater = MassUpdater(args, run_dir=run_dir, run_id=run_id)
        has_failures = updater.run()
        sys.exit(1 if has_failures else 0)
    except KeyboardInterrupt:
        os._exit(1)


if __name__ == '__main__':
    main()
