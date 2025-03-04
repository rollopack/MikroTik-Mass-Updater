#!/usr/bin/env python3

####################################################
#  MikroTik Mass Updater v4.5.1
#  Original Written by: Phillip Hutchison
#  Revamped version by: Kevin Byrd
#  Ported to Python and API by: Gabriel Rolland
####################################################

import threading
import queue
import time
import argparse
import librouteros

IP_LIST_FILE = 'list.txt'
LOG_FILE = 'backuplog.txt'
custom_commands = [];
# custom_commands = [
#     ('/user/add', {
#         'name': 'newuser',
#         'password': '#######',
#         'group': 'read'
#     }),
#     '/user/print',
# ]

parser = argparse.ArgumentParser(description="MikroTik Mass Updater")
parser.add_argument("-u", "--username", required=True, help="API username")
parser.add_argument("-p", "--password", required=True, help="API password")
parser.add_argument("-t", "--threads", type=int, default=10, help="Number of threads to use")
parser.add_argument("--timeout", type=int, default=15, help="Connection timeout in seconds")
parser.add_argument("--no-colors", action="store_true", help="Disable colored output")
parser.add_argument("--dry-run", action="store_true", help="Enable dry run mode (skip update installation)")
parser.add_argument("--start-line", type=int, default=1, help="Start from this line number in list.txt (1-based)")
args = parser.parse_args()

USE_COLORS = not args.no_colors

# Define ANSI color codes
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# Function to print with or without colors and force buffer flushing
def color_print(text, color=None, flush=True):
    if USE_COLORS and color:
        print(f"{color}{text}{Colors.ENDC}", flush=flush)
    else:
        print(text, flush=flush)

def execute_with_retry(api, command, params=None, max_retries=3, retry_delay=5):
    """Execute a command with retry logic"""
    last_exception = None
    for attempt in range(max_retries):
        try:
            if params is not None:
                return list(api(command, **params))
            return list(api(command))
        except (librouteros.exceptions.TimeoutError, librouteros.exceptions.ConnectionError) as e:
            last_exception = e
            color_print(f"Attempt {attempt + 1} failed: {e}", Colors.WARNING)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            raise last_exception

def parse_host_line(line):
    """Parse a line from list.txt and return (ip, port, username, password)"""
    # Format: IP[:PORT][|USERNAME|PASSWORD]
    parts = line.strip().split('|')
    
    # Parse IP and port
    ip_port = parts[0].split(':')
    ip = ip_port[0]
    port = int(ip_port[1]) if len(ip_port) > 1 else 8728
    
    # Parse optional credentials
    username = parts[1] if len(parts) > 1 else None
    password = parts[2] if len(parts) > 2 else None
    
    return ip, port, username, password

def worker(q, log, default_username, default_password, stop_event, timeout, dry_run):
    results = []
    while not stop_event.is_set():
        try:
            host_info = q.get(timeout=1)
            IP, port, custom_username, custom_password = host_info
            
            # Use custom credentials if provided, otherwise use defaults
            username = custom_username or default_username
            password = custom_password or default_password

        except queue.Empty:
            return

        entry_lines = []
        success = False
        api = None
        try:
            # Increase the connection timeout
            api = librouteros.connect(
                host=IP,
                username=username,
                password=password,
                port=int(port),
                timeout=max(30, timeout)  # Minimum 30 seconds for the connection
            )

            default_commands = [
                '/system/identity/print',
                '/system/routerboard/print',
                '/system/resource/print',
                '/system/package/update/check-for-updates',
                '/system/package/update/print',
            ]

            commands = default_commands + custom_commands

            for command in commands:
                try:
                    if isinstance(command, tuple):
                        cmd, params = command
                        response = execute_with_retry(api, cmd, params)
                    else:
                        response = execute_with_retry(api, command)
                    
                    if command == '/system/identity/print':
                        for res in response:
                            identity = res['name']
                            entry_lines.append(f"\nHost: {IP}\n  Identity: {identity}\n")
                    
                    elif command == '/system/routerboard/print':
                        for res in response:
                            model_name = res.get('board-name', res.get('model', 'N/A'))
                            entry_lines.append(f"  Model: {model_name}\n")
                    
                    elif command == '/system/resource/print':
                        for res in response:
                            version = res['version']
                            if 'stable' in res['version']:
                                version += " (stable)"
                            entry_lines.append(f"  Version: {version}\n")
                    
                    elif command == '/system/package/update/check-for-updates':
                        entry_lines.append("  Checking for updates...\n")
                        execute_with_retry(api, command)
                        # Wait for check to complete with increased timeout
                        for _ in range(15):  # increased from 10 to 15 attempts
                            time.sleep(2)  # increased from 1 to 2 seconds
                            try:
                                status_response = execute_with_retry(api, '/system/package/update/print', max_retries=2)
                                if status_response:
                                    status = status_response[0].get('status', '').lower()
                                    if 'checking' not in status:
                                        entry_lines.append(f"  Check-for-updates completed.\n  Status: {status}\n")
                                        break
                            except librouteros.exceptions.TimeoutError:
                                continue
                    
                    elif command == '/system/package/update/print':
                        for res in response:
                            status = res.get('status', '').lower()
                            channel = res.get('channel', '')
                            installed_version = res.get('installed-version', '')
                            latest_version = res.get('latest-version', '')

                            if latest_version and latest_version != installed_version:
                                entry_lines.append(f"  Updates available: {installed_version} -> {latest_version}\n")
                                try:
                                    if not dry_run:
                                        time.sleep(2)  # Short pause before updating
                                        update_package = api.path('system', 'package', 'update')
                                        execute_with_retry(update_package, 'install', max_retries=2)
                                        entry_lines.append(f"  Updates installed. Rebooting...\n")
                                    else:
                                        entry_lines.append(f"  Dry-run: Skipping installation of updates.\n")
                                    success = True
                                except Exception as e:
                                    entry_lines.append(f"  Error installing updates: {type(e).__name__}: {e}\n")
                                    success = False
                            else:
                                success = True

                except librouteros.exceptions.TimeoutError as e:
                    entry_lines.append(f"  Error executing command {command}: TimeoutError after retries\n")
                    success = False
                except Exception as e:
                    entry_lines.append(f"  Error executing command {command}: {type(e).__name__}: {e}\n")
                    success = False

        except Exception as e:
            entry_lines.append(f"\nHost: {IP}\n  Error: {type(e).__name__}: {e}\n")
        finally:
            entry = "".join(entry_lines)
            with log_lock:
                log.write(entry)
                log.flush()
                if success:
                    color_print(entry, Colors.OKGREEN)
                else:
                    color_print(entry + "  Result: Error", Colors.FAIL)
            q.task_done()
            results.append({"IP": IP, "success": success})

log_lock = threading.Lock()
q = queue.Queue()
threads = []
stop_event = threading.Event()

# Open the log file before the try-except block
log = open(LOG_FILE, 'w')

try:
    log.write("-- Starting job --\n")
    log.flush()

    color_print("-- Starting job --", Colors.UNDERLINE)

    with open(IP_LIST_FILE, 'r') as f:
        for i, line in enumerate(f, 1):
            if line.strip() and not line.startswith('#'):
                if i >= args.start_line:
                    host_info = parse_host_line(line)
                    q.put(host_info)

    for _ in range(args.threads):
        t = threading.Thread(target=worker, args=(q, log, args.username, args.password, stop_event, args.timeout, args.dry_run))
        threads.append(t)
        t.start()

    q.join()

except KeyboardInterrupt:
    color_print("Interrupted by user. Shutting down...", Colors.WARNING)
    stop_event.set()
finally:
    # Wait for all threads to finish
    for t in threads:
        t.join()

    # Close the log file after all threads are done
    log.write("\n-- Job finished --\n")
    log.flush()
    log.close()

color_print("\n-- Job finished --\n", Colors.UNDERLINE)