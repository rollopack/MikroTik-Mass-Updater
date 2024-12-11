#!/usr/bin/env python3

####################################################
#  MikroTik Mass Updater v3.0.10
#  Original Written by: Phillip Hutchison
#  Revamped version by: Kevin Byrd
#  Ported to Python and paramiko by: Gabriel Rolland
####################################################

import paramiko
import threading
import queue
import time
import argparse

IP_LIST_FILE = 'list.txt'
LOG_FILE = 'backuplog.txt'
MAX_THREADS = 5

# Aggiunta del parser degli argomenti
parser = argparse.ArgumentParser(description="MikroTik Mass Updater")
parser.add_argument("--no-colors", action="store_true", help="Disable colored output")
parser.add_argument("-u", "--username", required=True, help="SSH username")
parser.add_argument("-p", "--password", required=True, help="SSH password")
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

# Funzione per stampare con o senza colori e forzare lo svuotamento del buffer
def color_print(text, color=None, flush=True):
    if USE_COLORS and color:
        print(f"{color}{text}{Colors.ENDC}", flush=flush)
    else:
        print(text, flush=flush)

def worker(q, log, username, password):
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
            client.connect(hostname=IP, port=port, username=username, password=password, timeout=10, banner_timeout=10, auth_timeout=10, look_for_keys=False, allow_agent=False)

            commands = [
                '/system identity print',
                '/system package update check-for-updates',
                '/system package update install'
            ]

            for command in commands:
                # Sincronizza l'accesso al file di log con log_lock
                with log_lock:
                    log.write(f"Host: {IP}\n")  # Spostata qui
                    log.write(f"  Command: {command}\n")
                    log.flush()

                stdin, stdout, stderr = client.exec_command(command)
                stdout_str = stdout.read().decode()
                stderr_str = stderr.read().decode()

                # Scrivi nel log in tempo reale e forza lo svuotamento del buffer
                with log_lock:
                    if stdout_str:
                        log.write(f"    Output:\n{stdout_str}\n")
                    if stderr_str:
                        log.write(f"    Error:\n{stderr_str}\n")
                    log.flush()  # Forza la scrittura immediata

                host_log += f"  Command: {command}\n"
                if stdout_str:
                    host_log += f"    Output:\n{stdout_str}\n"
                if stderr_str:
                    host_log += f"    Error:\n{stderr_str}\n"

            client.close()
            success = True

        except Exception as e:
            # Gestisci l'eccezione e scrivi l'errore nel log in modo sicuro
            with log_lock:
                log.write(f"Host: {IP}\n") # Scrivi l'host in caso di errore
                log.write(f"  Error: {e}\n")
                log.write("  Result: encountered an error.\n\n")
                log.flush()
            color_print(f"Host: {IP}\n  Error: {e}\n  Result: encountered an error.\n", Colors.FAIL)
            q.task_done()
            continue  # Passa all'host successivo

        finally:
            with log_lock:
                if success:
                    log_entry = host_log + "  Result: updated successfully.\n\n"
                    # Scrivi solo la parte finale del risultato e forza lo svuotamento del buffer
                    log.write("  Result: updated successfully.\n\n")
                    log.flush()  # Forza la scrittura immediata
                    color_print(host_log + "  Result: updated successfully.\n", Colors.OKGREEN)
            q.task_done()
    while True:
        try:
            IP, port = q.get(timeout=1)
        except queue.Empty:
            return

        host_log = f"Host: {IP}\n"
        success = False

        try:
            # Scrivi "Host: {IP}\n" prima di tentare la connessione, all'interno del lock
            with log_lock:
                log.write(f"Host: {IP}\n")
                log.flush()
            
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=IP, port=port, username=username, password=password, timeout=10, banner_timeout=10, auth_timeout=10, look_for_keys=False, allow_agent=False)

            commands = [
                '/system identity print',
                '/system package update check-for-updates',
                '/system package update install'
            ]

            for command in commands:
                # Sincronizza l'accesso al file di log con log_lock
                with log_lock:
                    log.write(f"  Command: {command}\n")
                    log.flush()

                stdin, stdout, stderr = client.exec_command(command)
                stdout_str = stdout.read().decode()
                stderr_str = stderr.read().decode()

                # Scrivi nel log in tempo reale e forza lo svuotamento del buffer
                with log_lock:
                    if stdout_str:
                        log.write(f"    Output:\n{stdout_str}\n")
                    if stderr_str:
                        log.write(f"    Error:\n{stderr_str}\n")
                    log.flush()  # Forza la scrittura immediata

                host_log += f"  Command: {command}\n"
                if stdout_str:
                    host_log += f"    Output:\n{stdout_str}\n"
                if stderr_str:
                    host_log += f"    Error:\n{stderr_str}\n"

            client.close()
            success = True

        except Exception as e:
            host_log += f"  Error: {e}\n"
            # Gestisci l'eccezione e scrivi l'errore nel log in modo sicuro
            with log_lock:
                log.write(f"  Error: {e}\n")
                log.flush()
        finally:
            with log_lock:
                if success:
                    log_entry = host_log + "  Result: updated successfully.\n\n"
                    # Scrivi solo la parte finale del risultato e forza lo svuotamento del buffer
                    log.write("  Result: updated successfully.\n\n")
                    log.flush()  # Forza la scrittura immediata
                    color_print(host_log + "  Result: updated successfully.\n", Colors.OKGREEN)
                else:
                    log_entry = host_log + "  Result: encountered an error.\n\n"
                    # Scrivi solo la parte finale del risultato e forza lo svuotamento del buffer
                    log.write("  Result: encountered an error.\n\n")
                    log.flush()  # Forza la scrittura immediata
                    color_print(host_log + "  Result: encountered an error.\n", Colors.FAIL)
            q.task_done()

log_lock = threading.Lock() # Inizializzazione del lock per il log

# Inizializzazione della coda e dei thread
q = queue.Queue()
threads = []

# Apri il file di log una sola volta all'inizio
with open(LOG_FILE, 'w') as log:
    log.write("-- Starting job --\n\n")
    log.flush()

    color_print("-- Starting job --\n", Colors.OKGREEN)

    with open(IP_LIST_FILE, 'r') as f:
        for line in f:
            IP, _, port = line.strip().partition(':')
            q.put((IP, int(port or 22)))

    # Avvia i thread, passando l'oggetto 'log', username e password come argomento
    for _ in range(MAX_THREADS):
        t = threading.Thread(target=worker, args=(q, log, args.username, args.password))
        threads.append(t)
        t.start()

    # Attendi il completamento dei task
    q.join()

    # Attendi che i thread terminino
    for t in threads:
        t.join()

    # Scrivi la chiusura del log dopo che tutti i thread hanno terminato
    log.write("-- Job finished --\n")
    log.flush()

color_print("-- Job finished --\n", Colors.OKGREEN)