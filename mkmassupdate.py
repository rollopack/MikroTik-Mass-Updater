#!/usr/bin/env python3

####################################################
#  MikroTik Mass Updater v4.2
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

parser = argparse.ArgumentParser(description="MikroTik Mass Updater")
parser.add_argument("-u", "--username", required=True, help="API username")
parser.add_argument("-p", "--password", required=True, help="API password")
parser.add_argument("-t", "--threads", type=int, default=10, help="Number of threads to use")
parser.add_argument("--no-colors", action="store_true", help="Disable colored output")
args = parser.parse_args()

USE_COLORS = not args.no_colors

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def color_print(text, color=None, flush=True):
    if USE_COLORS and color:
        print(f"{color}{text}{Colors.ENDC}", flush=flush)
    else:
        print(text, flush=flush)

def worker(q, log, username, password, stop_event):
    while not stop_event.is_set():
        try:
            IP, port = q.get(timeout=1)
        except queue.Empty:
            return

        log_entry = ""
        print_entry = ""
        success = False
        api = None
        try:
            api = librouteros.connect(
                host=IP, 
                username=username, 
                password=password, 
                port=int(port),
                timeout=5
            )
            commands = [
                '/system/identity/print',
                '/system/resource/print',
                '/system/package/update/check-for-updates',
                '/system/package/update/print',
            ]

            for command in commands:
                response = api(command)
                if command == '/system/identity/print':
                    for res in response:
                        identity = res['name']
                        log_entry += f"\nHost: {IP}\n"
                        log_entry += f"  Identity: {identity}\n"
                        print_entry += f"Host: {IP}\n"
                        print_entry += f"  Identity: {identity}\n"
                elif command == '/system/resource/print':
                    for res in response:
                        version = res['version']
                        if 'stable' in res['version']:
                            version += " (stable)"
                        log_entry += f"  Version: {version}\n"
                        print_entry += f"  Version: {version}\n"
                elif command == '/system/package/update/check-for-updates':
                    log_entry += f"  Checking for updates...\n"
                    print_entry += f"  Checking for updates...\n"
                    while True:
                        status_response = api('/system/package/update/print')
                        if any('status' in res and 'checking' not in res['status'].lower() for res in status_response):
                            break
                        time.sleep(1)
                elif command == '/system/package/update/print':
                    for res in response:
                        status = res.get('status', '').lower()
                        if 'new version is available' in status:
                            log_entry += f"  Updates available for {IP}. Status: {status}\n"
                            print_entry += f"  Updates available for {IP}. Status: {status}\n"
                            try:
                                update_package = api.path('system', 'package', 'update')
                                tuple(update_package('install'))
                                log_entry += f"  Updates installed for {IP}. Rebooting...\n"
                                print_entry += f"  Updates installed for {IP}. Rebooting...\n"
                                success = True
                            except librouteros.exceptions.TrapError as e:
                                log_entry += f"  Error installing updates for {IP}: {e}\n"
                                print_entry += f"  Error installing updates for {IP}: {e}\n"
                        elif 'no updates available' in status or 'system is already up to date' in status:
                            log_entry += f"  No updates available for {IP}\n"
                            print_entry += f"  No updates available for {IP}\n"
                            success = True
                        else:
                            try:
                                update_package = api.path('system', 'package', 'update')
                                tuple(update_package('install'))
                                log_entry += f"  Updates installed for {IP}. Rebooting...\n"
                                print_entry += f"  Updates installed for {IP}. Rebooting...\n"
                                success = True
                            except librouteros.exceptions.TrapError as e:
                                log_entry += f"  Error installing updates for {IP}: {e}\n"
                                print_entry += f"  Error installing updates for {IP}: {e}\n"
        except (librouteros.exceptions.TrapError, librouteros.exceptions.FatalError, Exception) as e:
            log_entry += f"\nHost: {IP}\n"
            log_entry += f"  Error: {type(e).__name__}: {e}\n"
            log_entry += f"  Result: encountered an error.\n"
            print_entry += f"Host: {IP}\n  Error: {type(e).__name__}: {e}\n"
        finally:
            with log_lock:
                log.write(log_entry)
                log.flush()
                if success:
                    color_print(print_entry + "  Result: updated successfully.\n", Colors.OKGREEN)
                else:
                    color_print(print_entry + "  Result: encountered an error.\n", Colors.FAIL)
            q.task_done()

log_lock = threading.Lock()
q = queue.Queue()
threads = []
stop_event = threading.Event()

# Open the log file before the try-except block
log = open(LOG_FILE, 'w')

try:
    log.write("-- Starting job --\n\n")
    log.flush()

    color_print("-- Starting job --\n", Colors.UNDERLINE)

    with open(IP_LIST_FILE, 'r') as f:
        for line in f:
            IP, _, port = line.strip().partition(':')
            q.put((IP, int(port or 8728)))

    for _ in range(args.threads):
        t = threading.Thread(target=worker, args=(q, log, args.username, args.password, stop_event))
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
    log.write("-- Job finished --\n")
    log.flush()
    log.close()

color_print("-- Job finished --\n", Colors.UNDERLINE)