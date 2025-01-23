#!/usr/bin/env python3

####################################################
#  MikroTik Mass Updater v4.3.1
#  Original Written by: Phillip Hutchison
#  Revamped version by: Kevin Byrd
#  Ported to Python and API by: Gabriel Rolland
#  Optimized and corrected by: Bard
####################################################

import threading
import queue
import time
import argparse
import librouteros

IP_LIST_FILE = 'list.txt'
LOG_FILE = 'backuplog.txt'
custom_commands = [
    #'/ip/firewall/filter/print',
]

parser = argparse.ArgumentParser(description="MikroTik Mass Updater")
parser.add_argument("-u", "--username", required=True, help="API username")
parser.add_argument("-p", "--password", required=True, help="API password")
parser.add_argument("-t", "--threads", type=int, default=10, help="Number of threads to use")
parser.add_argument("--timeout", type=int, default=15, help="Connection timeout in seconds")
parser.add_argument("--no-colors", action="store_true", help="Disable colored output")
parser.add_argument("--dry-run", action="store_true", help="Enable dry run mode (skip update installation)")
args = parser.parse_args()

USE_COLORS = not args.no_colors

# Definisci codici colore ANSI
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# Funzione per stampare con o senza colori e forzare lo svuotamento del buffer
def color_print(text, color=None, flush=True):
    if USE_COLORS and color:
        print(f"{color}{text}{Colors.ENDC}", flush=flush)
    else:
        print(text, flush=flush)

def worker(q, log, username, password, stop_event, timeout, dry_run):
    results = []
    while not stop_event.is_set():
        try:
            IP, port = q.get(timeout=1)
        except queue.Empty:
            return

        entry_lines = []
        success = False
        api = None
        try:
            api = librouteros.connect(
                host=IP,
                username=username,
                password=password,
                port=int(port),
                timeout=timeout
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
                    if command == '/system/identity/print':
                        response = list(api(command))
                        for res in response:
                            identity = res['name']
                            entry_lines.append(f"\nHost: {IP}\n  Identity: {identity}\n")
                    
                    elif command == '/system/routerboard/print':
                        response = list(api(command))
                        for res in response:
                            model_name = res.get('board-name', res.get('model', 'N/A'))
                            entry_lines.append(f"  Model: {model_name}\n")
                    
                    elif command == '/system/resource/print':
                        response = list(api(command))
                        for res in response:
                            version = res['version']
                            if 'stable' in res['version']:
                                version += " (stable)"
                            entry_lines.append(f"  Version: {version}\n")
                    
                    elif command == '/system/package/update/check-for-updates':
                        entry_lines.append("  Checking for updates...\n")
                        list(api(command))  # Converti il generatore in lista
                        # Wait for check to complete
                        for _ in range(10):  # timeout after 10 attempts
                            time.sleep(1)
                            status_response = list(api('/system/package/update/print'))
                            if status_response:  # Verifica che ci sia almeno un elemento
                                status = status_response[0].get('status', '').lower()
                                if 'checking' not in status:
                                    entry_lines.append(f"  Check-for-updates completed. Status: {status}\n")
                                    break
                    
                    elif command == '/system/package/update/print':
                        response = list(api(command))
                        for res in response:
                            status = res.get('status', '').lower()
                            channel = res.get('channel', '')
                            installed_version = res.get('installed-version', '')
                            latest_version = res.get('latest-version', '')

                            if latest_version and latest_version != installed_version:
                                entry_lines.append(f"  Updates available: {installed_version} -> {latest_version}\n")
                                try:
                                    if not dry_run:
                                        update_package = api.path('system', 'package', 'update')
                                        list(update_package('install'))  # Converti il generatore in lista
                                        entry_lines.append(f"  Updates installed. Rebooting...\n")
                                    else:
                                        entry_lines.append(f"  Dry-run: Skipping installation of updates.\n")
                                    success = True
                                except librouteros.exceptions.TrapError as e:
                                    entry_lines.append(f"  Error installing updates: {e}\n")
                                    success = False
                            else:
                                success = True
                    
                    else:
                        response = list(api(command))  # Converti il generatore in lista
                        entry_lines.append(f"  Command: {command}\n")
                        entry_lines.append(f"    Output:\n")
                        for res in response:
                            entry_lines.append("      " + str(res) + "\n")

                except (librouteros.exceptions.TrapError, librouteros.exceptions.FatalError, Exception) as e:
                    entry_lines.append(f"  Error executing command {command}: {type(e).__name__}: {e}\n")
                    success = False

        except (librouteros.exceptions.TrapError, librouteros.exceptions.FatalError, Exception) as e:
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
        for line in f:
            IP, _, port = line.strip().partition(':')
            q.put((IP, int(port or 8728)))

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