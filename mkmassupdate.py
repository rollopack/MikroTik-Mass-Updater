#!/usr/bin/env python3

####################################################
#  MikroTik Mass Updater v5.0.6
#  Original Written by: Phillip Hutchison
#  Revamped version by: Kevin Byrd
#  Ported to Python and API by: Gabriel Rolland
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
import getpass
import yaml
from tqdm import tqdm
from librouteros.query import Key

# Lock globale per la sincronizzazione dei log e dei risultati
log_lock = threading.Lock()

# --- Logger setup definitions ---
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
    LOG_COLORS = {
        logging.DEBUG: Colors.OKBLUE,
        logging.INFO: Colors.OKGREEN,
        logging.WARNING: Colors.WARNING,
        logging.ERROR: Colors.FAIL,
        logging.CRITICAL: Colors.FAIL + Colors.BOLD,
    }

    CONSOLE_FORMAT = "%(message)s"
    CONSOLE_FORMAT_WITH_LEVEL = "%(levelname)s: %(message)s"

    def __init__(self, use_colors=True):
        super().__init__()
        self.use_colors = use_colors
        if self.use_colors:
            self.formatters = {
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

    def format(self, record):
        message_content = record.getMessage()
        if not message_content.strip():
            return ""
        formatter = self.formatters.get(record.levelno, self.default_formatter)
        return formatter.format(record)

class TqdmLoggingHandler(logging.StreamHandler):
    """Handler personalizzato per scrivere i log tramite tqdm senza rompere la progress bar"""
    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)

class NoEmptyMessagesFilter(logging.Filter):
    def filter(self, record):
        return bool(record.getMessage().strip())

def setup_logger(use_colors_arg, debug_level=False):
    logger_instance = logging.getLogger("MKMikroTikUpdater")
    logger_instance.setLevel(logging.DEBUG if debug_level else logging.INFO)
    logger_instance.propagate = False

    log_dir = 'log'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_filename = f"mkmassupdate-{time.strftime('%Y-%m-%d-%H-%M-%S')}.log"
    log_path = os.path.join(log_dir, log_filename)

    # File Handler
    fh = logging.FileHandler(log_path, mode='w', encoding='utf-8')
    fh_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')
    fh.setFormatter(fh_formatter)
    fh.addFilter(NoEmptyMessagesFilter())
    logger_instance.addHandler(fh)

    # Console Handler (Modificato per supportare Tqdm)
    ch = TqdmLoggingHandler()
    ch_formatter = ColoredFormatter(use_colors=use_colors_arg)
    ch.setFormatter(ch_formatter)
    logger_instance.addHandler(ch)
    
    return logger_instance

logger = logging.getLogger("MKMikroTikUpdater")

def execute_with_retry(api, command, params=None, max_retries=3, retry_delay=5):
    last_exception = None
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
            raise last_exception
        except librouteros.exceptions.TrapError as e:
            cmd_str = command[0] if isinstance(command, tuple) else command
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

def parse_host_line(line, default_api_port, default_ssl_port=8729):
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

def _connect_to_router(host_info, default_username, default_password, timeout, global_ssl=False):
    IP, port, custom_username, custom_password, use_ssl = host_info
    use_ssl = use_ssl or global_ssl
    username = custom_username or default_username
    password = custom_password or default_password
    effective_timeout = max(30, timeout)

    connect_kwargs = dict(
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

def _sanitize_command_item(command_item):
    """
    Sanitizes command_item to hide sensitive information like passwords.
    """
    if isinstance(command_item, tuple):
        cmd, params = command_item
        if isinstance(params, dict):
            sanitized_params = {}
            for k, v in params.items():
                if any(term in k.lower() for term in ['pass', 'secret']):
                    sanitized_params[k] = '********'
                else:
                    sanitized_params[k] = v
            return (cmd, sanitized_params)
    return command_item

def _execute_router_command(api, command_item, entry_lines):
    try:
        if isinstance(command_item, tuple):
            cmd, params = command_item
            response = execute_with_retry(api, cmd, params)
        else:
            cmd = command_item
            response = execute_with_retry(api, cmd)
        return response
    except (TimeoutError, socket.error) as e:
        sanitized_item = _sanitize_command_item(command_item)
        entry_lines.append(f"  Error executing command {sanitized_item}: TimeoutError after retries\n")
    except Exception as e:
        sanitized_item = _sanitize_command_item(command_item)
        entry_lines.append(f"  Error executing command {sanitized_item}: {type(e).__name__}: {e}\n")
    return None

def _process_identity(response, entry_lines):
    if response:
        for res in response:
            identity = res.get('name', 'N/A')
            entry_lines.append(f"  Identity: {identity}\n")

def _process_routerboard(response, entry_lines):
    if response:
        for res in response:
            model_name = res.get('board-name', res.get('model', 'N/A'))
            entry_lines.append(f"  Model: {model_name}\n")

def _process_resource(response, entry_lines):
    if response:
        for res in response:
            version = res.get('version', 'N/A')
            if 'stable' in res.get('build-time', ''): 
                version = f"{version} (stable)"
            entry_lines.append(f"  Version: {version}\n")

def _check_and_process_updates(api, entry_lines, dry_run, check_attempts, check_delay):
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
                    entry_lines.append(f"  Updates installed. Rebooting...\n")
                    return True
                except Exception as e:
                    entry_lines.append(f"  Error installing updates: {type(e).__name__}: {e}\n")
                    return False
            else:
                entry_lines.append(f"  Dry-run: Skipping installation of updates.\n")
    
    return False

def _perform_cloud_backup(api, cloud_password, entry_lines):
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

    upload_params = {
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

def _reboot_router(api, entry_lines):
    reboot_script_name = "mkmassupdate_reboot"
    try:
        name_key = Key('name')
        scripts = list(api.path('/system', 'script').select(name_key).where(name_key == reboot_script_name))
        if scripts is None:
            entry_lines.append(f"  Failed to check for existing script '{reboot_script_name}'. Aborting reboot.\n")
            return

        if not scripts:
            add_script_params = {
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

def _perform_firmware_upgrade(api, entry_lines):
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
        #entry_lines.append("  Firmware upgrade: Attempting to upgrade...\n")
        upgrade_response = _execute_router_command(api, '/system/routerboard/upgrade', entry_lines)
        if upgrade_response is None:
            entry_lines.append("  Firmware upgrade: Failed.\n")
            return False
        entry_lines.append("  Firmware upgrade: Upgrade command sent.\n")
        return True

def worker(q, default_username, default_password, cloud_password, stop_event, timeout, dry_run, aggregated_results_list, update_check_attempts, update_check_delay, upgrade_firmware, pbar, custom_commands, global_ssl=False):
    api = None
    while not stop_event.is_set():
        entry_lines = []
        success = False
        IP = None

        try:
            host_info = q.get(timeout=1)
            IP, _, _, _, _ = host_info
            entry_lines.append(f"\nHost: {IP}\n")

            api = _connect_to_router(host_info, default_username, default_password, timeout, global_ssl)

            default_commands_map = {
                '/system/identity/print': _process_identity,
                '/system/routerboard/print': _process_routerboard,
                '/system/resource/print': _process_resource,
            }

            all_commands_to_process = [
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
            
            if not command_execution_successful:
                success = False
            else:
                success = True
                if cloud_password:
                    backup_success = _perform_cloud_backup(api, cloud_password, entry_lines)
                    if not backup_success:
                        entry_lines.append("  Warning: Cloud backup failed. Proceeding with updates regardless.\n")

                firmware_upgraded = False
                if success and upgrade_firmware:
                    firmware_upgrade_status = _perform_firmware_upgrade(api, entry_lines)
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
        except librouteros.exceptions.TrapError as e:
            error_message = f"  Error: {type(e).__name__}: {e.message} (code: {e.code})\n"
            if e.message and not str(e.code) in e.message:
                 error_message = f"  Error: {e.message}\n"
            entry_lines.append(error_message)
            success = False
        except (TimeoutError, socket.error) as e:
            entry_lines.append(f"  Error: Connection failed - {type(e).__name__}: {e}\n")
            success = False
        except queue.Empty:
            if stop_event.is_set():
                logger.debug(f"Worker {threading.current_thread().name} exiting due to stop_event.")
            return
        except Exception as e:
            if IP:
                 entry_lines.append(f"  Unexpected error processing host {IP}: {type(e).__name__}: {e}\n")
            else:
                 entry_lines.append(f"  Unexpected error: {type(e).__name__}: {e}\n")
            success = False
        finally:
            if api:
                try:
                    api.close()
                except Exception:
                    pass

            if not entry_lines and IP: 
                entry_lines.append(f"\nHost: {IP}\n  No operations performed or error before logging started.\n")
            
            final_entry_text = "".join(entry_lines).strip() 

            with log_lock:
                if final_entry_text:
                    logger.info("-" * 70)

                if success:
                    logger.info(final_entry_text)
                else:
                    logger.error(final_entry_text)

                if IP is not None:
                    aggregated_results_list.append({"IP": IP, "success": success})

            if not stop_event.is_set(): 
                try:
                    q.task_done()
                except ValueError: 
                    logger.debug(f"ValueError on q.task_done() in {threading.current_thread().name}.")
                    pass
            
            if IP is not None:
                pbar.update(1)

q = queue.Queue()
threads = []
stop_event = threading.Event()
aggregated_results = []

def main():
    parser = argparse.ArgumentParser(description="MikroTik Mass Updater")
    parser.add_argument("-u", "--username", required=True, help="API username")
    parser.add_argument("-p", "--password", help="API password. If not provided, it will be asked for securely.")
    parser.add_argument("-t", "--threads", type=int, default=5, help="Number of threads to use")
    parser.add_argument("--timeout", type=int, default=5, help="Connection timeout in seconds")
    parser.add_argument("--ip-list", default='list.txt', help="Path to the IP list file.")
    parser.add_argument("--port", type=int, default=8728, help="Default API port.")
    parser.add_argument("--update-check-attempts", type=int, default=15, help="Number of attempts to check update status.")
    parser.add_argument("--update-check-delay", type=float, default=2.0, help="Delay between update status checks.")
    parser.add_argument("--no-colors", action="store_true", help="Disable colored output")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry run mode")
    parser.add_argument("--start-line", type=int, default=1, help="Start from this line number")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging level.")
    parser.add_argument("--cloud-password", help="Password for cloud backup")
    parser.add_argument("--upgrade-firmware", action="store_true", help="Perform firmware upgrade")
    parser.add_argument("--ssl", action="store_true", help="Enable SSL for all connections")
    parser.add_argument("--custom-commands", help="Path to a YAML file with custom commands.")
    args = parser.parse_args()

    if not args.password:
        args.password = getpass.getpass(f"Enter password for user '{args.username}': ")

    if args.ssl and args.port == 8728:
        args.port = 8729

    setup_logger(not args.no_colors, args.debug)
    pbar = None
    
    custom_commands = []
    if args.custom_commands:
        try:
            with open(args.custom_commands, 'r') as f:
                loaded_commands = yaml.safe_load(f)
                if loaded_commands:
                    for item in loaded_commands:
                        if 'params' in item:
                            custom_commands.append((item['command'], item['params']))
                        else:
                            custom_commands.append(item['command'])
            logger.info(f"Loaded {len(custom_commands)} custom commands from {args.custom_commands}")
        except FileNotFoundError:
            logger.error(f"Custom commands file not found: {args.custom_commands}")
        except Exception as e:
            logger.error(f"Error parsing custom commands file: {e}")

    try:
        logger.info("-- Starting job --")

        try:
            with open(args.ip_list, 'r') as f:
                lines = [line for i, line in enumerate(f, 1) if i >= args.start_line and line.strip() and not line.strip().startswith('#')]
            total_hosts = len(lines)
            pbar = tqdm(total=total_hosts, desc="Processing hosts", unit="host")
        except FileNotFoundError:
            logger.error(f"IP list file not found: {args.ip_list}")
            return

        for _ in range(args.threads):
            t = threading.Thread(
                target=worker,
                args=(
                    q, args.username, args.password, args.cloud_password, stop_event, args.timeout, args.dry_run,
                    aggregated_results, args.update_check_attempts, args.update_check_delay, args.upgrade_firmware, pbar, custom_commands, args.ssl
                ),
                name=f"Worker-{_+1}"
            )
            threads.append(t)
            t.start()

        for line_content in lines:
            if stop_event.is_set():
                logger.warning("Interruption detected, stopping queue population.")
                break
            host_info = parse_host_line(line_content, args.port)
            if host_info:
                q.put(host_info)

        while not q.empty() and not stop_event.is_set():
            time.sleep(0.1)

        if not stop_event.is_set():
            q.join()

    except KeyboardInterrupt:
        logger.warning("\nInterrupted by user. Shutting down gracefully...")
        stop_event.set()

    finally:
        if pbar:
            pbar.close()

        if stop_event.is_set():
            logger.warning("Clearing queue due to interruption...")
            while not q.empty():
                try:
                    q.get_nowait()
                    q.task_done()
                except queue.Empty:
                    break
                except ValueError:
                    break

        for t in threads:
            t.join()

        # --- Output Summary Ottimizzato ---
        total_hosts_processed = len(aggregated_results)
        successful_ops = sum(1 for res in aggregated_results if res["success"])
        failed_ops = total_hosts_processed - successful_ops
        failed_ips = [res["IP"] for res in aggregated_results if not res["success"]]

        # Utilizzo delle multi-line f-strings per un blocco visivo pulito e leggibile
        summary_output = (
            f"\n\n========================================\n"
            f"             JOB SUMMARY                \n"
            f"========================================\n"
            f" Total hosts processed : {total_hosts_processed}\n"
            f" Successful operations : {successful_ops}\n"
            f" Failed operations     : {failed_ops}\n"
            f"========================================"
        )

        if failed_ops > 0:
            summary_output += "\n Failed IPs:\n"
            for specific_ip in failed_ips:
                if specific_ip != "Unknown (worker exited early)":
                    summary_output += f"  ❌ - {specific_ip}\n"
            summary_output += "========================================"

        logger.info(summary_output)
        logger.info("-- Job finished --")
        logging.shutdown()

if __name__ == '__main__':
    main()