#!/usr/bin/env python3

####################################################
#  MikroTik Mass Updater v4.1.5
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
MAX_THREADS = 5

# Aggiunta del parser degli argomenti
parser = argparse.ArgumentParser(description="MikroTik Mass Updater")
parser.add_argument("--no-colors", action="store_true", help="Disable colored output")
parser.add_argument("-u", "--username", required=True, help="API username")
parser.add_argument("-p", "--password", required=True, help="API password")
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

        log_entry = ""  # Inizializza la stringa per l'intero log dell'host
        print_entry = "" # Inizializza la stringa per l'intero print dell'host
        success = False
        api = None
        try:
            # Connessione API sincrona usando librouteros
            api = librouteros.connect(host=IP, username=username, password=password, port=int(port))

            commands = [
                '/system/identity/print',
                '/system/resource/print',
                '/system/package/update/check-for-updates',
                '/system/package/update/print',
            ]

            for command in commands:
                # Esegui il comando e ottieni la risposta
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
                    # Delay introdotto per dare il tempo al router di elaborare la richiesta
                    time.sleep(3)
                elif command == '/system/package/update/print':
                    for res in response:
                        status = res.get('status', '').lower()
                        if 'new version is available' in status:
                            log_entry += f"  Updates available for {IP}. Status: {status}\n"
                            print_entry += f"  Updates available for {IP}. Status: {status}\n"

                            # Installa gli aggiornamenti
                            try:
                                update_package = api.path('system', 'package', 'update')
                                tuple(update_package('install'))

                                log_entry += f"  Updates installed for {IP}. Rebooting...\n"
                                print_entry += f"  Updates installed for {IP}. Rebooting...\n"
                                success = True  # Aggiornamento riuscito
                            except librouteros.exceptions.TrapError as e:
                                log_entry += f"  Error installing updates for {IP}: {e}\n"
                                print_entry += f"  Error installing updates for {IP}: {e}\n"
                        elif 'no updates available' in status or 'system is already up to date' in status:
                            log_entry += f"  No updates available for {IP}\n"
                            print_entry += f"  No updates available for {IP}\n"
                            success = True
                        else:
                            log_entry += f"  Status: {status}\n"
                            print_entry += f"  Status: {status}\n"

                            # Installa comunque gli aggiornamenti
                            try:
                                update_package = api.path('system', 'package', 'update')
                                tuple(update_package('install'))

                                log_entry += f"  Updates installed for {IP}. Rebooting...\n"
                                print_entry += f"  Updates installed for {IP}. Rebooting...\n"
                                success = True  # Aggiornamento riuscito
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
                log.write(log_entry)  # Scrivi l'intero log_entry dell'host nel file di log
                log.flush()
                if success:
                    color_print(print_entry + "  Result: updated successfully.\n", Colors.OKGREEN)
                else:
                    color_print(print_entry + "  Result: encountered an error.\n", Colors.FAIL)
            q.task_done()

log_lock = threading.Lock()

# Inizializzazione della coda e dei thread
q = queue.Queue()
threads = []

# Apri il file di log una sola volta all'inizio
with open(LOG_FILE, 'w') as log:
    log.write("-- Starting job --\n\n")
    log.flush()

    color_print("-- Starting job --\n", Colors.UNDERLINE)

    with open(IP_LIST_FILE, 'r') as f:
        for line in f:
            IP, _, port = line.strip().partition(':')
            q.put((IP, int(port or 8728)))

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

color_print("-- Job finished --\n", Colors.UNDERLINE)