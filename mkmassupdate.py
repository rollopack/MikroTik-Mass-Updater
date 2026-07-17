#!/usr/bin/env python3

from __future__ import annotations

####################################################
#  MikroTik Mass Updater v5.2.2
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
from typing import Any
from tqdm import tqdm
from librouteros.query import Key

log_lock = threading.Lock()


class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


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
        if not self.use_colors:
             self.formatters[logging.INFO] = logging.Formatter(self.CONSOLE_FORMAT)

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


def _setup_logger(use_colors_arg: bool, debug_level: bool = False) -> logging.Logger:
    logger_instance = logging.getLogger("MKMikroTikUpdater")
    logger_instance.setLevel(logging.DEBUG if debug_level else logging.INFO)
    logger_instance.propagate = False

    log_dir = 'log'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_filename = f"mkmassupdate-{time.strftime('%Y-%m-%d-%H-%M-%S')}.log"
    log_path = os.path.join(log_dir, log_filename)

    fh = logging.FileHandler(log_path, mode='w', encoding='utf-8')
    fh_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')
    fh.setFormatter(fh_formatter)
    fh.addFilter(NoEmptyMessagesFilter())
    logger_instance.addHandler(fh)

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
) -> list[dict[str, Any]] | None:
    last_exception: Exception | None = None
    for attempt in range(max_retries):
        try:
            if params is not None:
                return list(api(command, **params))
            return list(api(command))
        except (TimeoutError, socket.error, librouteros.exceptions.LibRouterosError) as e:
            last_exception = e
            logger.warning(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            if last_exception:
                raise last_exception
        except librouteros.exceptions.TrapError as e:
            cmd_str: Any = command[0] if isinstance(command, tuple) else command
            is_cloud_command = isinstance(cmd_str, str) and 'cloud' in cmd_str

            msg = getattr(e, 'message', '') or str(e)
            msg_lower = msg.lower()
            is_transient = any(term in msg_lower for term in ['connection', 'timeout', 'connect', 'resolve'])

            if is_cloud_command and is_transient:
                last_exception = e
                logger.warning(f"Attempt {attempt + 1} failed for cloud command '{cmd_str}' due to transient TrapError: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
            raise e
    return None


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
    effective_timeout = max(30, timeout)

    connect_kwargs: dict[str, Any] = dict(
        host=IP,
        username=username,
        password=password,
        port=int(port),
        timeout=effective_timeout
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
            response = execute_with_retry(api, cmd, params)
        else:
            response = execute_with_retry(api, command_item)
        return response
    except (TimeoutError, socket.error) as e:
        sanitized_item = _sanitize_command_item(command_item)
        entry_lines.append(f"  Error executing command {sanitized_item}: TimeoutError after retries\n")
    except Exception as e:
        sanitized_item = _sanitize_command_item(command_item)
        entry_lines.append(f"  Error executing command {sanitized_item}: {type(e).__name__}: {e}\n")
    return None


def _process_identity(response: list[dict[str, Any]], entry_lines: list[str]) -> None:
    if response:
        for res in response:
            identity = res.get('name', 'N/A')
            entry_lines.append(f"  Identity: {identity}\n")


def _process_routerboard(response: list[dict[str, Any]], entry_lines: list[str]) -> None:
    if response:
        for res in response:
            model_name = res.get('board-name', res.get('model', 'N/A'))
            entry_lines.append(f"  Model: {model_name}\n")


def _process_resource(response: list[dict[str, Any]], entry_lines: list[str]) -> None:
    if response:
        for res in response:
            version = res.get('version', 'N/A')
            if 'stable' in res.get('build-time', ''):
                version = f"{version} (stable)"
            entry_lines.append(f"  Version: {version}\n")


def _check_and_process_updates(
    api: librouteros.Connection,
    entry_lines: list[str],
    dry_run: bool,
    check_attempts: int,
    check_delay: float,
) -> bool:
    entry_lines.append("  Checking for updates...\n")
    response = _execute_router_command(api, '/system/package/update/check-for-updates', entry_lines)
    if response is None:
        return False

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
            return False

    if not check_complete:
        entry_lines.append("  Timeout waiting for update check to complete.\n")
        return False

    status_response = _execute_router_command(api, '/system/package/update/print', entry_lines)
    if not status_response:
        return False

    for res in status_response:
        installed_version = res.get('installed-version', '')
        latest_version = res.get('latest-version', '')

        if latest_version and latest_version != installed_version:
            entry_lines.append(f"  Updates available: {installed_version} -> {latest_version}\n")
            if not dry_run:
                time.sleep(2)
                try:
                    update_package_path = api.path('system', 'package', 'update')
                    execute_with_retry(update_package_path, 'install', max_retries=2)
                    entry_lines.append("  Updates installed. Rebooting...\n")
                    return True
                except Exception as e:
                    entry_lines.append(f"  Error installing updates: {type(e).__name__}: {e}\n")
                    return False
            else:
                entry_lines.append("  Dry-run: Skipping installation of updates.\n")

    return False


def _perform_cloud_backup(
    api: librouteros.Connection,
    cloud_password: str,
    entry_lines: list[str],
    dry_run: bool = False,
) -> bool:
    if dry_run:
        entry_lines.append("  Cloud backup: Dry-run — would create and upload backup.\n")
        return True

    # Sleep 3s to let slow cloud connections stabilize before querying existing backups
    time.sleep(3)
    existing_backups = _execute_router_command(api, '/system/backup/cloud/print', entry_lines)
    if existing_backups is None:
        entry_lines.append("  Cloud backup: Failed to retrieve list of existing backups. Aborting.\n")
        return False

    if existing_backups:
        backup_ids = [backup['.id'] for backup in existing_backups if '.id' in backup]
        if backup_ids:
            all_removed_successfully = True
            for backup_id in backup_ids:
                remove_params = {'number': backup_id}
                response_remove = _execute_router_command(api, ('/system/backup/cloud/remove-file', remove_params), entry_lines)
                if response_remove is None:
                    all_removed_successfully = False
            if not all_removed_successfully:
                return False

    upload_params: dict[str, str] = {
        'action': 'create-and-upload',
        'password': cloud_password
    }
    response_upload = _execute_router_command(api, ('/system/backup/cloud/upload-file', upload_params), entry_lines)
    if response_upload is None:
        entry_lines.append("  Cloud backup: Failed to create and upload new backup.\n")
        return False

    entry_lines.append("  Cloud backup: Successfully created and uploaded new backup.\n")
    time.sleep(2)
    latest_backups = _execute_router_command(api, '/system/backup/cloud/print', entry_lines)

    if latest_backups:
        latest_backup = latest_backups[0]
        secret_key = latest_backup.get('secret-download-key')
        if secret_key:
            entry_lines.append(f"  Cloud backup: Secret Download Key: {secret_key}\n")
        else:
            entry_lines.append("  Cloud backup: Could not find secret-download-key for the latest backup.\n")
    else:
        entry_lines.append("  Cloud backup: Failed to retrieve backup details to get secret key.\n")

    return True


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


def _perform_firmware_upgrade(
    api: librouteros.Connection,
    entry_lines: list[str],
    dry_run: bool = False,
) -> bool | None:
    routerboard_info = _execute_router_command(api, '/system/routerboard/print', entry_lines)
    if not routerboard_info:
        entry_lines.append("  Firmware upgrade: Failed to retrieve routerboard information. Aborting.\n")
        return False

    info = routerboard_info[0]
    current_firmware = info.get('current-firmware')
    upgrade_firmware = info.get('upgrade-firmware')

    if not current_firmware or not upgrade_firmware:
        entry_lines.append("  Firmware upgrade: Could not determine firmware versions. Aborting.\n")
        return False

    if current_firmware == upgrade_firmware:
        entry_lines.append(f"  Firmware is up to date (current: {current_firmware}). No upgrade needed.\n")
        return None
    else:
        entry_lines.append(f"  Firmware upgrade available: {current_firmware} -> {upgrade_firmware}\n")
        if dry_run:
            entry_lines.append("  Firmware upgrade: Dry-run — skipping upgrade command.\n")
            return None
        upgrade_response = _execute_router_command(api, '/system/routerboard/upgrade', entry_lines)
        if upgrade_response is None:
            entry_lines.append("  Firmware upgrade: Failed.\n")
            return False
        entry_lines.append("  Firmware upgrade: Upgrade command sent.\n")
        return True


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


class MassUpdater:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.q: queue.Queue[tuple[str, int, str | None, str | None, bool]] = queue.Queue()
        self.threads: list[threading.Thread] = []
        self.stop_event: threading.Event = threading.Event()
        self.aggregated_results: list[dict[str, Any]] = []
        self._start_time: float = 0.0
        self._processed_count: int = 0
        self._success_count: int = 0

    def _load_custom_commands(self) -> list:
        custom_commands: list = []
        if self.args.custom_commands:
            try:
                with open(self.args.custom_commands, 'r', encoding='utf-8') as f:
                    loaded_commands = yaml.safe_load(f)
                    if loaded_commands:
                        for item in loaded_commands:
                            if 'params' in item:
                                custom_commands.append((item['command'], item['params']))
                            else:
                                custom_commands.append(item['command'])
                logger.info(f"Loaded {len(custom_commands)} custom commands from {self.args.custom_commands}")
            except FileNotFoundError:
                logger.error(f"Custom commands file not found: {self.args.custom_commands}")
            except Exception as e:
                logger.error(f"Error parsing custom commands file: {e}")
        return custom_commands

    def _load_ip_list(self) -> list[str]:
        with open(self.args.ip_list, 'r', encoding='utf-8') as f:
            lines = [
                line for i, line in enumerate(f, 1)
                if i >= self.args.start_line and line.strip() and not line.strip().startswith('#')
            ]
        return lines

    def _run_commands_on_router(
        self,
        api: librouteros.Connection,
        custom_commands: list,
        entry_lines: list[str],
    ) -> bool:
        default_commands_map: dict[str, Any] = {
            '/system/identity/print': _process_identity,
            '/system/routerboard/print': _process_routerboard,
            '/system/resource/print': _process_resource,
        }

        all_commands_to_process: list = [
            '/system/identity/print',
            '/system/routerboard/print',
            '/system/resource/print',
        ] + custom_commands

        command_execution_successful = True
        for command_item in all_commands_to_process:
            command_path = command_item[0] if isinstance(command_item, tuple) else command_item
            response = _execute_router_command(api, command_item, entry_lines)
            if response is None:
                command_execution_successful = False
                continue

            if command_path in default_commands_map:
                default_commands_map[command_path](response, entry_lines)
            else:
                entry_lines.append(f"  Response for {command_path}:\n")
                for res_item in response:
                    entry_lines.append(f"    {res_item}\n")

        return command_execution_successful

    def _process_host(
        self,
        host_info: tuple[str, int, str | None, str | None, bool],
        custom_commands: list,
        cloud_password: str | None,
        upgrade_firmware: bool,
        dry_run: bool,
        update_check_attempts: int,
        update_check_delay: float,
        timeout: int,
        global_ssl: bool,
        default_username: str,
        default_password: str,
    ) -> tuple[bool, list[str]]:
        entry_lines: list[str] = []
        IP, port, _, _, _ = host_info
        entry_lines.append(f"\nHost: {IP}\n")
        api: librouteros.Connection | None = None

        try:
            api = _connect_to_router(host_info, default_username, default_password, timeout, global_ssl)

            commands_ok = self._run_commands_on_router(api, custom_commands, entry_lines)
            if not commands_ok:
                return False, entry_lines

            success = True
            if cloud_password:
                backup_success = _perform_cloud_backup(api, cloud_password, entry_lines, dry_run)
                if not backup_success:
                    entry_lines.append("  Warning: Cloud backup failed. Proceeding with updates regardless.\n")

            firmware_upgraded = False
            if success and upgrade_firmware:
                firmware_upgrade_status = _perform_firmware_upgrade(api, entry_lines, dry_run)
                if firmware_upgrade_status is False:
                    success = False
                elif firmware_upgrade_status is True:
                    firmware_upgraded = True

            if success:
                reboot_triggered = _check_and_process_updates(
                    api, entry_lines, dry_run, update_check_attempts, update_check_delay
                )
                if not reboot_triggered and firmware_upgraded:
                    _reboot_router(api, entry_lines)

            return success, entry_lines

        except librouteros.exceptions.TrapError as e:
            msg = getattr(e, 'message', '') or str(e)
            entry_lines.append(f"  Error: {msg}\n")
            return False, entry_lines
        except TimeoutError:
            entry_lines.append(f"  Error: Connection timed out ({IP}:{port})\n")
            return False, entry_lines
        except socket.error as e:
            entry_lines.append(f"  Error: Connection failed ({IP}:{port}) - {e.strerror or e}\n")
            return False, entry_lines
        except Exception as e:
            if IP:
                 entry_lines.append(f"  Unexpected error processing host {IP}: {type(e).__name__}: {e}\n")
            else:
                 entry_lines.append(f"  Unexpected error: {type(e).__name__}: {e}\n")
            return False, entry_lines
        finally:
            if api:
                try:
                    api.close()
                except Exception:
                    pass

    def _worker(
        self,
        default_username: str,
        default_password: str,
        cloud_password: str | None,
        timeout: int,
        dry_run: bool,
        update_check_attempts: int,
        update_check_delay: float,
        upgrade_firmware: bool,
        pbar: tqdm[Any],
        custom_commands: list,
        global_ssl: bool,
    ) -> None:
        while not self.stop_event.is_set():
            try:
                host_info = self.q.get(timeout=1)
            except queue.Empty:
                if self.stop_event.is_set():
                    logger.debug(f"Worker {threading.current_thread().name} exiting due to stop_event.")
                return

            IP, _, _, _, _ = host_info

            success, entry_lines = self._process_host(
                host_info, custom_commands, cloud_password, upgrade_firmware,
                dry_run, update_check_attempts, update_check_delay,
                timeout, global_ssl, default_username, default_password,
            )

            if not entry_lines:
                entry_lines = [f"\nHost: {IP}\n  No operations performed or error before logging started.\n"]

            final_entry_text = "".join(entry_lines).strip()

            with log_lock:
                if final_entry_text:
                    logger.info("─" * 50)
                if success:
                    logger.info(final_entry_text)
                else:
                    logger.error(final_entry_text)

                self.aggregated_results.append({"IP": IP, "success": success})
                self._processed_count += 1
                if success:
                    self._success_count += 1
                fail_count = self._processed_count - self._success_count
                pbar.set_postfix(ok=self._success_count, fail=fail_count)
                pbar.update(1)

            if not self.stop_event.is_set():
                try:
                    self.q.task_done()
                except ValueError:
                    logger.debug(f"ValueError on q.task_done() in {threading.current_thread().name}.")

    def _start_workers(
        self,
        thread_count: int,
        pbar: tqdm[Any],
        custom_commands: list,
    ) -> None:
        for i in range(thread_count):
            t = threading.Thread(
                target=self._worker,
                args=(
                    self.args.username, self.args.password, self.args.cloud_password,
                    self.args.timeout, self.args.dry_run,
                    self.args.update_check_attempts, self.args.update_check_delay,
                    self.args.upgrade_firmware, pbar, custom_commands, self.args.ssl
                ),
                name=f"Worker-{i + 1}"
            )
            self.threads.append(t)
            t.start()

    def _populate_queue(self, lines: list[str]) -> None:
        for line_content in lines:
            if self.stop_event.is_set():
                logger.warning("Interruption detected, stopping queue population.")
                break
            host_info = parse_host_line(line_content, self.args.port)
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
            while not self.q.empty():
                try:
                    self.q.get_nowait()
                    self.q.task_done()
                except (queue.Empty, ValueError):
                    break

    def _join_threads(self) -> None:
        for t in self.threads:
            while t.is_alive():
                t.join(timeout=1)

    def _print_summary(self) -> bool:
        total_hosts_processed = len(self.aggregated_results)
        successful_ops = sum(1 for res in self.aggregated_results if res["success"])
        failed_ops = total_hosts_processed - successful_ops
        failed_ips = [res["IP"] for res in self.aggregated_results if not res["success"]]
        elapsed = time.time() - self._start_time

        title = "JOB SUMMARY"
        if self.args.dry_run:
            title = "JOB SUMMARY (DRY RUN)"

        summary_lines = [
            f"\n\n========================================",
            f"             {title}                ",
            f"========================================",
            f" Total hosts processed : {total_hosts_processed}",
            f" Successful operations : {successful_ops}",
            f" Failed operations     : {failed_ops}",
            f" Elapsed time          : {elapsed:.1f}s",
            f"========================================",
        ]

        logger.info("\n".join(summary_lines))

        if failed_ops > 0:
            logger.info(" Failed IPs:")
            for specific_ip in failed_ips:
                if specific_ip != "Unknown (worker exited early)":
                    logger.error(f"  [FAIL] - {specific_ip}")
            logger.info("========================================")
        logger.info("-- Job finished --")
        logging.shutdown()
        return failed_ops > 0

    def run(self) -> bool:
        custom_commands = self._load_custom_commands()
        pbar: tqdm[Any] | None = None
        file_not_found = False
        self._start_time = time.time()

        try:
            logger.info("-- Starting job --")

            try:
                lines = self._load_ip_list()
            except FileNotFoundError:
                logger.error(f"IP list file not found: {self.args.ip_list}")
                file_not_found = True
                return True

            total_hosts = len(lines)
            desc = "[DRY RUN] Processing hosts" if self.args.dry_run else "Processing hosts"
            pbar = tqdm(total=total_hosts, desc=desc, unit="host")
            pbar.set_postfix(ok=0, fail=0)

            self._start_workers(self.args.threads, pbar, custom_commands)
            self._populate_queue(lines)
            self._wait_for_completion()

        except KeyboardInterrupt:
            self._handle_interrupt()
        finally:
            if pbar:
                pbar.close()
            self._cleanup_after_interrupt()
            self._join_threads()
            if not file_not_found:
                return self._print_summary()
        return True


def _apply_config_file(parser: argparse.ArgumentParser) -> None:
    known, _ = parser.parse_known_args()
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

    valid_keys = {a.dest for a in parser._actions if hasattr(a, 'dest') and a.dest != 'help'}
    filtered = {k: v for k, v in cfg.items() if k in valid_keys}
    if filtered:
        parser.set_defaults(**filtered)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MikroTik Mass Updater")
    parser.add_argument("-u", "--username", help="API username")
    parser.add_argument("-p", "--password", help="API password. If not provided, it will be asked for securely.")
    parser.add_argument("-t", "--threads", type=_positive_int, default=5, help="Number of threads to use (min: 1)")
    parser.add_argument("--timeout", type=_positive_int, default=5, help="Connection timeout in seconds (min: 1, effectively clamped to 30)")
    parser.add_argument("--ip-list", default='list.txt', help="Path to the IP list file.")
    parser.add_argument("--port", type=_port_type, default=8728, help="Default API port (1-65535).")
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
    parser.add_argument("--config", help="Path to a YAML configuration file. CLI arguments override config file values.")
    parser.add_argument("--version", action="version", version="5.2.0")

    _apply_config_file(parser)
    args = parser.parse_args()

    if not args.username:
        parser.error("the following arguments are required: -u/--username")

    return args


def main() -> None:
    try:
        args = _parse_args()

        if not args.password:
            args.password = getpass.getpass(f"Enter password for user '{args.username}': ")

        if args.ssl and args.port == 8728:
            args.port = 8729

        _setup_logger(not args.no_colors, args.debug)

        updater = MassUpdater(args)
        has_failures = updater.run()
        sys.exit(1 if has_failures else 0)
    except KeyboardInterrupt:
        os._exit(1)


if __name__ == '__main__':
    main()
