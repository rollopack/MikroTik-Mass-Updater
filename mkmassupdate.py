#!/usr/bin/env python3

####################################################
#  MikroTik Mass Updater v3.0.1
#  Original Written by: Phillip Hutchison
#  Revamped version by: Kevin Byrd 
#  Ported to Python and paramiko by: Gabriel Rolland
####################################################

import paramiko
import threading
import queue
import time
import argparse

USERNAME = 'admin'
PASSWORD = 'password'
IP_LIST_FILE = 'list.txt'
LOG_FILE = 'backuplog.txt'
MAX_THREADS = 5

log_lock = threading.Lock()

# Aggiunta del parser degli argomenti
parser = argparse.ArgumentParser(description="MikroTik Mass Updater")
parser.add_argument("--no-colors", action="store_true", help="Disable colored output")
args = parser.parse_args()

# Usa i colori solo se --no-colors NON è presente
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

# Funzione per stampare con o senza colori
def color_print(text, color=None):
    if USE_COLORS and color:
        print(f"{color}{text}{Colors.ENDC}")
    else:
        print(text)

def worker(q, log):
    while True:
        try:
            IP, port = q.get(timeout=1)
        except queue.Empty:
            return

        host_log = f"Host: {IP}\n"
        success = False

        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=IP, port=port, username=USERNAME, password=PASSWORD, timeout=10, banner_timeout=10, auth_timeout=10, look_for_keys=False, allow_agent=False)

            commands = [
                '/system identity print',
                '/system package update check-for-updates',
                '/system package update install'
            ]

            for command in commands:
                stdin, stdout, stderr = client.exec_command(command)
                stdout_str = stdout.read().decode()
                stderr_str = stderr.read().decode()

                host_log += f"  Command: {command}\n"
                if stdout_str:
                    host_log += f"    Output:\n{stdout_str}\n"
                if stderr_str:
                    host_log += f"    Error:\n{stderr_str}\n"

            client.close()
            success = True

        except Exception as e:
            host_log += f"  Error: {e}\n"
        finally:
            with log_lock:
                if success:
                    log_entry = host_log + "  Result: updated successfully.\n\n"
                    log.write(log_entry)
                    color_print(host_log + "  Result: updated successfully.\n", Colors.OKGREEN)
                else:
                    log_entry = host_log + "  Result: encountered an error.\n\n"
                    log.write(log_entry)
                    color_print(host_log + "  Result: encountered an error.\n", Colors.FAIL)
            q.task_done()

with open(LOG_FILE, 'w') as log:
    q = queue.Queue()
    threads = []

    log.write("-- Starting job --\n\n")
    color_print("-- Starting job --\n", Colors.OKGREEN)

    with open(IP_LIST_FILE, 'r') as f:
        for line in f:
            IP, _, port = line.strip().partition(':')
            q.put((IP, int(port or 22)))

    for _ in range(MAX_THREADS):
        t = threading.Thread(target=worker, args=(q, log))
        threads.append(t)
        t.start()

    q.join()

    for t in threads:
        t.join()

    log.write("-- Job finished --\n")
    color_print("-- Job finished --\n", Colors.OKGREEN)