#!/usr/bin/env python3

####################################################
#  MikroTik Mass Updater v4.9
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
import logging # Import logging module

custom_commands = [];
#custom_commands = [
#     ('/user/add', {
#         'name': 'newuser',
#         'password': '#######',
#         'group': 'read'
#     }),
#     '/user/print',
#     '/system/clock/print'
# ]

# --- Logger setup definitions ---
# Define ANSI color codes (used by ColoredFormatter)
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# Custom Formatter for colored logging
class ColoredFormatter(logging.Formatter):
    LOG_COLORS = {
        logging.DEBUG: Colors.OKBLUE, # Using Colors class
        logging.INFO: Colors.OKGREEN,   # Using Colors class
        logging.WARNING: Colors.WARNING, # Using Colors class
        logging.ERROR: Colors.FAIL,   # Using Colors class
        logging.CRITICAL: Colors.FAIL + Colors.BOLD, # Using Colors class
    }
    # Using Colors class defined below (now above)
    # Ensure Colors class is defined before this Formatter if referenced directly by name.
    # For now, using hardcoded ANSI codes matching the Colors class.

    CONSOLE_FORMAT = "%(message)s" # Default for console, mimics old print behavior
    CONSOLE_FORMAT_WITH_LEVEL = "%(levelname)s: %(message)s" # For levels other than INFO on console

    def __init__(self, use_colors=True):
        super().__init__()
        self.use_colors = use_colors
        # Re-initialize LOG_COLORS here to ensure Colors class members are accessible
        # if this class definition is moved before Colors. But with correct ordering, direct reference is fine.
        # For safety and to ensure it uses the *actual* Colors class values:
        self.LOG_COLORS = {
            logging.DEBUG: Colors.OKBLUE,
            logging.INFO: Colors.OKGREEN,
            logging.WARNING: Colors.WARNING,
            logging.ERROR: Colors.FAIL,
            logging.CRITICAL: Colors.FAIL + Colors.BOLD,
        }

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
        
        # Default formatter for levels not in LOG_COLORS or when colors are off
        self.default_formatter = logging.Formatter(self.CONSOLE_FORMAT_WITH_LEVEL)
        if not self.use_colors: # For INFO level when colors are off, ensure no levelname
             self.formatters[logging.INFO] = logging.Formatter(self.CONSOLE_FORMAT)


    def format(self, record):
        # Get the actual message string after any formatting arguments have been applied.
        message_content = record.getMessage()

        # Check if the stripped message content is empty.
        if not message_content.strip():
            return ""  # Return an empty string to suppress all output for this record.

        # If there is actual content, proceed with normal colorized formatting.
        formatter = self.formatters.get(record.levelno, self.default_formatter)
        return formatter.format(record)

# Custom filter to prevent empty messages in file logs
class NoEmptyMessagesFilter(logging.Filter):
    def filter(self, record):
        # Return True to process the record, False to discard it.
        # We discard if the stripped message is empty.
        return bool(record.getMessage().strip())

def setup_logger(log_path, use_colors_arg, debug_level=False):
    logger_instance = logging.getLogger("MKMikroTikUpdater")
    logger_instance.setLevel(logging.DEBUG if debug_level else logging.INFO)
    logger_instance.propagate = False # Prevent root logger from handling messages in parent loggers

    # File Handler (always logs with no colors, includes thread names)
    fh = logging.FileHandler(log_path, mode='w', encoding='utf-8')
    fh_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')
    fh.setFormatter(fh_formatter)
    
    # Add the custom filter to the file handler
    fh.addFilter(NoEmptyMessagesFilter()) # <--- ADDED THIS LINE
    
    logger_instance.addHandler(fh)

    # Console Handler
    ch = logging.StreamHandler()
    # No need to re-assign ColoredFormatter.LOG_COLORS here as it's done in __init__
    ch_formatter = ColoredFormatter(use_colors=use_colors_arg) # Pass use_colors_arg

    ch.setFormatter(ch_formatter)
    logger_instance.addHandler(ch)
    
    return logger_instance
# --- End of logger setup definitions ---

# Initialize logger (now after definitions and args parsing)
logger = logging.getLogger("MKMikroTikUpdater")

# This section is now clean after previous refactoring.

def execute_with_retry(api, command, params=None, max_retries=3, retry_delay=5):
    """
    Executes a librouteros API command with a specified number of retries on failure.
    Handles TimeoutError and socket.error for retry attempts.
    """
    last_exception = None
    for attempt in range(max_retries):
        try:
            if params is not None:
                return list(api(command, **params))
            return list(api(command))
        except (TimeoutError, socket.error) as e: # <--- Changed this line
            last_exception = e
            logger.warning(f"Attempt {attempt + 1} failed: {e}") # Replaced color_print
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            raise last_exception

def parse_host_line(line, default_api_port):
    """
    Parses a single line from the IP list file.
    Expected format: IP[:PORT][|USERNAME|PASSWORD]
    Returns a tuple (ip, port, username, password) or None on parsing error.
    """
    stripped_line = line.strip()
    try:
        # Format: IP[:PORT][|USERNAME|PASSWORD]
        parts = stripped_line.split('|')
        
        ip_port_str = parts[0]
        if not ip_port_str:
            raise ValueError("IP/Port part is empty")

        ip_port_parts = ip_port_str.split(':')
        ip = ip_port_parts[0]
        if not ip:
            raise ValueError("IP address cannot be empty")
        
        port_str = ip_port_parts[1] if len(ip_port_parts) > 1 else str(default_api_port) # Use default_api_port
        port = int(port_str) # This can raise ValueError if port_str is not a valid integer
        if not (1 <= port <= 65535):
            raise ValueError(f"Port number {port} out of range (1-65535)")
        
        # Parse optional credentials
        username = parts[1] if len(parts) > 1 else None
        password = parts[2] if len(parts) > 2 else None
        
        return ip, port, username, password
    except ValueError as e:
        logger.warning(f"Skipping malformed line in IP list: '{stripped_line}'. Error: {e}") # Replaced color_print
        return None
    except IndexError:
        # This can happen if parts[0] is fine, but parts[1] or parts[2] are accessed without existing
        # (e.g. "1.2.3.4|" or just "1.2.3.4" and code tries to access username/password beyond available parts)
        # Or if ip_port_parts[1] is accessed when there's no ':' in ip_port_str (handled by default port_str logic, but good to be safe)
        logger.warning(f"Skipping malformed line due to incorrect format: '{stripped_line}'. Check pipe and colon separators.") # Replaced color_print
        return None


# Helper functions for worker

def _connect_to_router(host_info, default_username, default_password, timeout):
    """
    Establishes a connection to the MikroTik router using librouteros.
    Selects custom credentials if provided in host_info, otherwise uses defaults.
    Uses a minimum connection timeout of 30 seconds or the user-specified timeout, whichever is greater.
    Returns the API connection object.
    """
    IP, port, custom_username, custom_password = host_info
    username = custom_username or default_username
    password = custom_password or default_password

    # Using max() ensures a robust minimum timeout for the initial connection phase.
    effective_timeout = max(30, timeout) 
    api = librouteros.connect(
        host=IP,
        username=username,
        password=password,
        port=int(port),
        timeout=effective_timeout
    )
    return api

def _execute_router_command(api, command_item, entry_lines):
    """
    Executes a single router command using the provided API connection.
    Handles retries via `execute_with_retry` and appends errors to `entry_lines`.
    `command_item` can be a string (command path) or a tuple (command path, parameters).
    Returns the command response or None if an error occurs.
    """
    try:
        if isinstance(command_item, tuple):
            cmd, params = command_item
            response = execute_with_retry(api, cmd, params)
        else:
            cmd = command_item
            response = execute_with_retry(api, cmd)
        return response
    except (TimeoutError, socket.error) as e: # Catch generic TimeoutError and socket.error
        entry_lines.append(f"  Error executing command {command_item}: TimeoutError after retries\n")
    except Exception as e:
        entry_lines.append(f"  Error executing command {command_item}: {type(e).__name__}: {e}\n")
    return None

def _process_identity(response, entry_lines):
    """Processes the response from /system/identity/print."""
    if response:
        for res in response:
            identity = res.get('name', 'N/A') # Use .get for safer dictionary access
            entry_lines.append(f"  Identity: {identity}\n")

def _process_routerboard(response, entry_lines):
    """Processes and appends routerboard information from the command response to entry_lines."""
    if response:
        for res in response:
            model_name = res.get('board-name', res.get('model', 'N/A')) # Handle potential missing keys
            entry_lines.append(f"  Model: {model_name}\n")

def _process_resource(response, entry_lines):
    """Processes and appends system resource information (version) from the command response to entry_lines."""
    if response:
        for res in response:
            version = res.get('version', 'N/A')
            # The 'stable' check was based on 'build-time' or similar, this might need review
            # if 'stable' appears elsewhere in the actual response structure.
            # For now, keeping original logic:
            if 'stable' in res.get('build-time', ''): 
                version = f"{version} (stable)" # Use f-string
            entry_lines.append(f"  Version: {version}\n")

def _check_and_process_updates(api, entry_lines, dry_run, check_attempts, check_delay):
    """
    Handles the full update process for a router:
    1. Initiates '/system/package/update/check-for-updates'.
    2. Polls '/system/package/update/print' to monitor status until completion or timeout.
    3. If updates are available and not in dry_run mode, attempts to install them.
    Appends relevant status messages to `entry_lines`.
    Returns True if the update process completed successfully (including no updates needed or dry run), 
    False otherwise.
    """
    update_success = False  # Assume failure until a positive outcome

    entry_lines.append("  Checking for updates...\n")
    # Initial check-for-updates call
    response = _execute_router_command(api, '/system/package/update/check-for-updates', entry_lines)
    if response is None: # Error already logged by _execute_router_command
        return False

    # Wait for check to complete
    check_complete = False
    for _ in range(check_attempts):  # Use new parameter
        time.sleep(check_delay)  # Use new parameter
        status_response = _execute_router_command(api, '/system/package/update/print', entry_lines)
        if status_response:
            status = status_response[0].get('status', '').lower()
            if 'checking' not in status:
                entry_lines.append(f"  Status: {status}\n")
                check_complete = True
                break
        else: # Error during status check
            return False # Error already logged by _execute_router_command
    
    if not check_complete:
        entry_lines.append("  Timeout waiting for update check to complete.\n")
        return False

    # Process update status
    status_response = _execute_router_command(api, '/system/package/update/print', entry_lines)
    if not status_response:
        return False # Error already logged

    for res in status_response:
        # status = res.get('status', '').lower() # Status already captured above
        # channel = res.get('channel', '') # Not used in original decision making for update
        installed_version = res.get('installed-version', '')
        latest_version = res.get('latest-version', '')

        if latest_version and latest_version != installed_version:
            entry_lines.append(f"  Updates available: {installed_version} -> {latest_version}\n")
            if not dry_run:
                time.sleep(2)  # Short pause before updating
                # Using api.path directly as it's a specific kind of command execution
                # _execute_router_command might need adjustment if we want to use it for path objects.
                # For now, direct call to execute_with_retry for install, as in original.
                try:
                    update_package_path = api.path('system', 'package', 'update')
                    execute_with_retry(update_package_path, 'install', max_retries=2) # Original had max_retries=2 here
                    entry_lines.append(f"  Updates installed. Rebooting...\n")
                    update_success = True
                except Exception as e:
                    entry_lines.append(f"  Error installing updates: {type(e).__name__}: {e}\n")
                    update_success = False
            else:
                entry_lines.append(f"  Dry-run: Skipping installation of updates.\n")
                update_success = True # Dry run is a "successful" outcome in terms of processing
        else:
            # entry_lines.append(f"  No new updates available or already up-to-date.\n") # <--- REMOVED THIS LINE
            update_success = True # No action needed is also a success
    
    return update_success

# Implemented cloud backup function
def _perform_cloud_backup(api, cloud_password, entry_lines):
    #entry_lines.append("  Cloud backup: Checking for existing backups...\n")

    # 1. Get a list of all existing cloud backups.
    existing_backups = _execute_router_command(api, '/system/backup/cloud/print', entry_lines)

    if existing_backups is None:
        # Error executing the print command is already logged by _execute_router_command.
        entry_lines.append("  Cloud backup: Failed to retrieve list of existing backups. Aborting.\n")
        return False

    # 2. If backups exist, collect their IDs and remove them all in a single command.
    if existing_backups:
        backup_ids = [backup['.id'] for backup in existing_backups if '.id' in backup]
        
        if backup_ids:
            #entry_lines.append(f"  Cloud backup: Found {len(backup_ids)} existing backup(s). Removing...\n")
            all_removed_successfully = True
            for backup_id in backup_ids:
                # Use singular 'number' and iterate to remove each backup individually for compatibility.
                remove_params = {'number': backup_id}
                response_remove = _execute_router_command(api, ('/system/backup/cloud/remove-file', remove_params), entry_lines)
                if response_remove is None:
                    # Error is logged by _execute_router_command, just mark failure and continue.
                    all_removed_successfully = False

            if not all_removed_successfully:
                #entry_lines.append("  Cloud backup: Failed to remove one or more existing backups.\n")
                return False
            #else:
                #entry_lines.append("  Cloud backup: Successfully removed all existing backups.\n")
    #else:
        #entry_lines.append("  Cloud backup: No previous backups found.\n")

    # 3. Create and upload new backup
    #entry_lines.append("  Cloud backup: Creating and uploading new backup...\n")
    upload_params = {
        'action': 'create-and-upload',
        'password': cloud_password
    }
    response_upload = _execute_router_command(api, ('/system/backup/cloud/upload-file', upload_params), entry_lines)

    if response_upload is None:
        entry_lines.append("  Cloud backup: Failed to create and upload new backup.\n")
        return False
    
    entry_lines.append("  Cloud backup: Successfully created and uploaded new backup.\n")

    # After successful backup, retrieve the secret-download-key
    time.sleep(2) # Give the router a moment to process
    #entry_lines.append("  Cloud backup: Retrieving secret download key...\n")
    latest_backups = _execute_router_command(api, '/system/backup/cloud/print', entry_lines)

    if latest_backups:
        # Assuming the first backup in the list is the one just created
        latest_backup = latest_backups[0]
        secret_key = latest_backup.get('secret-download-key')
        if secret_key:
            entry_lines.append(f"  Cloud backup: Secret Download Key: {secret_key}\n")
        else:
            entry_lines.append("  Cloud backup: Could not find secret-download-key for the latest backup.\n")
    else:
        entry_lines.append("  Cloud backup: Failed to retrieve backup details to get secret key.\n")

    return True

def _perform_firmware_upgrade(api, entry_lines):
    """
    Checks for and performs a firmware upgrade if one is available.
    A reboot is issued upon successful upgrade, which will cause a disconnect.
    """
    #entry_lines.append("  Firmware upgrade: Checking status...\n")
    
    # 1. Get routerboard info to check versions
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

    # 2. Compare versions and decide if upgrade is needed
    if current_firmware == upgrade_firmware:
        entry_lines.append(f"  Firmware is up to date (current: {current_firmware}). No upgrade needed.\n")
        return True # Success, no action needed.
    else:
        entry_lines.append(f"  Firmware upgrade available: {current_firmware} -> {upgrade_firmware}\n")
        
        # 3. Perform the upgrade
        entry_lines.append("  Firmware upgrade: Attempting to upgrade...\n")
        try:
            # The API command for upgrade should not be interactive.
            upgrade_response = _execute_router_command(api, '/system/routerboard/upgrade', entry_lines)
            if upgrade_response is None:
                 # Error is already logged by _execute_router_command
                 entry_lines.append("  Firmware upgrade: Failed.\n")
                 return False

            # If the command was sent, a reboot is required.
            entry_lines.append("  Firmware upgrade: Upgrade command sent. Rebooting router to apply...\n")
            
            # 4. Reboot the router. This will cause an exception.
            try:
                # We don't use _execute_router_command here because we expect an exception
                # and don't want it to be logged as a failure in the same way.
                _execute_router_command(api, '/system/reboot', entry_lines)
                time.sleep(1) # Give router time to process reboot before connection is closed.
                # We might not reach here if the reboot is instantaneous.
            except (socket.error, TimeoutError, ConnectionResetError) as e:
                # This is the expected outcome of a successful reboot command.
                entry_lines.append(f"  Router is rebooting as expected. Disconnected.\n")
                # We can consider this a success because the command was sent.
                return True
            except Exception as e:
                # Any other exception is unexpected.
                entry_lines.append(f"  Firmware upgrade: An unexpected error occurred during reboot: {type(e).__name__}: {e}\n")
                return False

            # If we somehow get here without an exception, it's still a success as the command was sent.
            entry_lines.append("  Reboot command sent. Router will reboot shortly.\n")
            return True

        except Exception as e:
            entry_lines.append(f"  Firmware upgrade: An error occurred: {type(e).__name__}: {e}\n")
            return False

def worker(q, default_username, default_password, cloud_password, stop_event, timeout, dry_run, aggregated_results_list, update_check_attempts, update_check_delay, upgrade_firmware): # Modified signature
    # results = # This was removed in a previous step.
    api = None # Initialize api to None for each worker function call scope.
    
    while not stop_event.is_set():
        entry_lines = [] # Stores log messages for the current host.
        success = False  # Assume failure for the current host processing.
        IP = None        # IP address of the current host.
                         # API connection object, specific to this host processing attempt.
                         # Resetting api to None for each host attempt inside the loop is safer,
                         # but given it's assigned in the try block, it effectively is.
                         # The function-scoped `api` initialized outside the loop handles cases
                         # where q.get() might fail before api is assigned in the try.
                         # Current `api` is function-scoped, so it persists if not reassigned.
                         # Let's keep it as is, if `_connect_to_router` fails, `api` remains from previous or None.
                         # The `finally` block correctly handles `if api:` before closing.

        try:
            host_info = q.get(timeout=1) # Get host from queue with timeout.
            IP, _, _, _ = host_info      # Unpack host info.
            
            entry_lines.append(f"\nHost: {IP}\n") # Start log entry for this host.

            # Attempt to connect to the router.
            api = _connect_to_router(host_info, default_username, default_password, timeout)

            # Define mapping for standard informational commands.
            default_commands_map = {
                '/system/identity/print': _process_identity,
                '/system/routerboard/print': _process_routerboard,
                '/system/resource/print': _process_resource,
            }

            # Combine default and custom commands for processing.
            all_commands_to_process = [
                '/system/identity/print',
                '/system/routerboard/print',
                '/system/resource/print',
            ] + custom_commands

            command_execution_successful = True # Flag to track if all commands execute without error.

            # Process each command.
            for command_item in all_commands_to_process:
                command_path = command_item[0] if isinstance(command_item, tuple) else command_item
                
                response = _execute_router_command(api, command_item, entry_lines)
                if response is None: # Error already logged by _execute_router_command
                    command_execution_successful = False
                    # Original behavior was to continue processing other commands for the host.
                    continue 

                # If command is a standard one, use its specific processor.
                if command_path in default_commands_map:
                    default_commands_map[command_path](response, entry_lines)
                else:
                    # Generic handling for custom commands.
                    entry_lines.append(f"  Response for {command_path}:\n")
                    for res_item in response: 
                        entry_lines.append(f"    {res_item}\n")
            
            # If any command failed, mark overall success for this host as false.
            if not command_execution_successful:
                success = False
            else:
                # All informational/custom commands were successful.
                # Start with success=True and set to False if any subsequent step fails.
                success = True

                # Perform cloud backup if password is provided
                if cloud_password:
                    backup_success = _perform_cloud_backup(api, cloud_password, entry_lines)
                    if not backup_success:
                        success = False # Mark overall success as False if backup failed

                # If backup was successful (or not performed), proceed to check/process updates.
                if success:
                    success = _check_and_process_updates(
                        api, entry_lines, dry_run, update_check_attempts, update_check_delay
                    )
                
                if success and upgrade_firmware:
                    firmware_success = _perform_firmware_upgrade(api, entry_lines)
                    if not firmware_success:
                        success = False
        except librouteros.exceptions.TrapError as e:
            # Handle specific API errors like authentication failure.
            # Using f-string for error message.
            error_message = f"  Error: {type(e).__name__}: {e.message} (code: {e.code})\n"
            if e.message and not str(e.code) in e.message: # Avoid duplicating code in message
                 error_message = f"  Error: {e.message}\n"
            entry_lines.append(error_message)
            success = False
        except (TimeoutError, socket.error) as e:
            # Handle connection errors (timeout, socket issues).
            entry_lines.append(f"  Error: Connection failed - {type(e).__name__}: {e}\n")
            success = False
        except queue.Empty:
            # Worker timed out waiting for a new item; normal for shutdown or empty queue.
            if stop_event.is_set(): # Log if stopping, otherwise it's just a timeout
                logger.debug(f"Worker {threading.current_thread().name} exiting due to stop_event and empty queue.")
            return # Exit worker thread.
        except Exception as e:
            # Catch-all for other unexpected errors during host processing.
            if IP:
                 entry_lines.append(f"  Unexpected error processing host {IP}: {type(e).__name__}: {e}\n")
            else:
                 entry_lines.append(f"  Unexpected error: {type(e).__name__}: {e}\n")
            success = False
        finally:
            # This block executes regardless of exceptions in the try block.
            # Ensure API connection is closed if it was opened.
            if api:
                try:
                    api.close()
                    # Ensure no "API connection closed" message is appended here.
                except Exception as e_close:
                    # Suppress errors from api.close() by doing nothing.
                    pass

            # Ensure some log output if entry_lines is empty for some reason.
            if not entry_lines and IP: 
                # This case might still be relevant if a connection was attempted for a known IP
                # but failed very early, before any specific messages were added.
                entry_lines.append(f"\nHost: {IP}\n  No operations performed or error before logging started.\n")
            # REMOVED the "elif not entry_lines and not IP:" block to suppress "Unknown Host" messages.
            # elif not entry_lines and not IP: 
            #      entry_lines.append("\nUnknown Host\n  No operations performed or error before logging started.\n")
            
            final_entry_text = "".join(entry_lines).strip() 

            with log_lock: # Protects shared resources: logger and aggregated_results_list
                if final_entry_text: # Only add separator if there is actual content for the host
                    logger.info("-" * 70) # Log a separator line (REMOVED leading "\n")

                if success:
                    logger.info(final_entry_text)
                else:
                    logger.error(final_entry_text)

                # Only append to results if an IP was actually assigned and processed in this iteration.
                if IP is not None:
                    # current_ip_for_results is not strictly needed anymore if we only append when IP is not None.
                    # We can directly use IP.
                    aggregated_results_list.append({"IP": IP, "success": success})
                else:
                    # This 'else' block is for workers that hit queue.Empty before processing an IP.
                    # They should not contribute to aggregated_results.
                    # If there's a need to know how many workers exited idly, that's a different metric,
                    # not part of "hosts processed".
                    pass 

            if not stop_event.is_set(): 
                try:
                    q.task_done()
                except ValueError: 
                    # This can happen if task_done() is called on an empty queue,
                    # especially if stop_event was set and queue was cleared in main.
                    logger.debug(f"ValueError on q.task_done() in {threading.current_thread().name}. Queue might be empty.")
                    pass

log_lock = threading.Lock() # Lock for synchronizing access to shared resources (aggregated_results)
q = queue.Queue() # Queue for distributing host information to worker threads.
threads = [] # List to keep track of worker threads.
stop_event = threading.Event() # Event to signal worker threads to stop.
aggregated_results = [] # Shared list to store results from all worker threads.

# Main script execution block
def main():
    parser = argparse.ArgumentParser(description="MikroTik Mass Updater")
    parser.add_argument("-u", "--username", required=True, help="API username")
    parser.add_argument("-p", "--password", required=True, help="API password")
    parser.add_argument("-t", "--threads", type=int, default=5, help="Number of threads to use")
    parser.add_argument("--timeout", type=int, default=5, help="Connection timeout in seconds")
    parser.add_argument("--ip-list", default='list.txt', help="Path to the IP list file.")
    parser.add_argument("--log-file", default='backuplog.txt', help="Path to the log file.")
    parser.add_argument(
        "--port",
        type=int,
        default=8728,
        help="Default API port to use if not specified in the IP list file (e.g., IP:PORT)."
    )
    parser.add_argument(
        "--update-check-attempts",
        type=int,
        default=15,
        help="Number of attempts to check update status after issuing check-for-updates."
    )
    parser.add_argument(
        "--update-check-delay",
        type=float,
        default=2.0,
        help="Delay (in seconds) between update status checks."
    )
    parser.add_argument("--no-colors", action="store_true", help="Disable colored output")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry run mode (skip update installation)")
    parser.add_argument("--start-line", type=int, default=1, help="Start from this line number in list.txt (1-based)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging level.") # Add --debug argument
    parser.add_argument("--cloud-password", help="Password for cloud backup")
    parser.add_argument("--upgrade-firmware", action="store_true", help="Perform firmware upgrade")
    args = parser.parse_args()

    setup_logger(args.log_file, not args.no_colors, args.debug)

    try:
        logger.info("-- Starting job --")

        # Start worker threads.
        for _ in range(args.threads):
            # Removed log from args
            t = threading.Thread(
                target=worker,
                args=(
                    q, args.username, args.password, args.cloud_password, stop_event, args.timeout, args.dry_run,
                    aggregated_results, args.update_check_attempts, args.update_check_delay, args.upgrade_firmware
                ),
                name=f"Worker-{_+1}"
            )
            threads.append(t)
            t.start()

        # Populate the queue with host information from the IP list file.
        try:
            with open(args.ip_list, 'r') as f:
                for i, line_content in enumerate(f, 1): # Renamed 'line' to 'line_content'
                    if stop_event.is_set():
                        logger.warning("Interruption detected, stopping queue population.")
                        break
                    stripped_line_for_check = line_content.strip()
                    if stripped_line_for_check and not stripped_line_for_check.startswith('#'):
                        if i >= args.start_line:
                            host_info = parse_host_line(line_content, args.port)
                            if host_info:
                                q.put(host_info)
        except FileNotFoundError:
            logger.error(f"IP list file not found: {args.ip_list}")
            stop_event.set() # Signal threads to stop if IP list is not found.

        # Wait for the queue to be processed or for an interruption.
        while not q.empty() and not stop_event.is_set():
            time.sleep(0.1)

        # If not interrupted, wait for all tasks in the queue to be completed.
        if not stop_event.is_set() and not q.empty():
            logger.info("Waiting for all tasks in queue to complete normally...")
            q.join()
        elif not stop_event.is_set() and q.empty():
            # This means all items were processed and q.join() is not strictly needed
            # as workers would have called task_done for each item they processed from the queue.
            # However, calling q.join() on an empty queue that previously had items
            # and for which all task_done() calls were made is non-blocking and safe.
            # It ensures all tasks are accounted for if the queue became empty very quickly.
            logger.debug("Queue is empty, all tasks presumed processed.")
            q.join()


    except KeyboardInterrupt:
        logger.warning("\nInterrupted by user. Shutting down gracefully...")
        stop_event.set() # Signal worker threads to stop.

    finally:
        # This block ensures cleanup and reporting happen reliably.
        #logger.info("Waiting for worker threads to finish...")

        # If interrupted, clear any remaining items from the queue.
        if stop_event.is_set():
            logger.warning("Clearing queue due to interruption...")
            while not q.empty():
                try:
                    q.get_nowait() # Remove item without blocking.
                    q.task_done()  # Mark as done to allow q.join() later if it were used.
                except queue.Empty:
                    logger.debug("Queue is now empty during interrupt cleanup.")
                    break
                except ValueError:
                    logger.debug("ValueError during queue cleanup, q.task_done() called too many times.")
                    break # Should not happen if task_done only called after get_nowait.

        # Wait for all worker threads to complete their current tasks and exit.
        for t in threads:
            t.join()

        # Process and print the job summary.
        total_hosts = len(aggregated_results)
        successful_ops = sum(1 for res in aggregated_results if res["success"])
        failed_ops = total_hosts - successful_ops
        failed_ips = [res["IP"] for res in aggregated_results if not res["success"]]

        summary_lines = []
        summary_lines.append("\n\n-- Job Summary --\n")
        summary_lines.append(f"Total hosts processed: {total_hosts}\n")
        summary_lines.append(f"Successful operations: {successful_ops}\n")
        summary_lines.append(f"Failed operations: {failed_ops}\n")

        if failed_ops > 0:
            summary_lines.append("Failed IPs:\n") # Keep this header

            unknown_host_marker = "Unknown (worker exited early)"
            # We still need to filter them out from being individually listed if they were part of failed_ips
            specific_failed_ips = [ip for ip in failed_ips if ip != unknown_host_marker]

            # The line counting unknown_host_count and appending it is simply removed.
            # No 'if unknown_host_count > 0:' block that appends to summary_message_lines.

            for specific_ip in specific_failed_ips:
                summary_lines.append(f"  - {specific_ip}\n")

        summary_output = "".join(summary_lines) # This already contains the desired structure with newlines.

        # Consolidate into a single logger call for the entire summary.
        # The summary_output string already has a leading "\n\n-- Job Summary --\n"
        # and subsequent lines are newline-terminated.
        # We just need to ensure it's logged once.
        # The ColoredFormatter for INFO level on console is just "%(message)s",
        # so it will print the multi-line string as is.
        # The file logger will also log it as a multi-line string.
        logger.info(summary_output.strip()) # Use strip() to remove any trailing newline from the last item, if any.
                                          # The initial "\n\n" will provide spacing.

        logger.info("-- Job finished --") # For file and console.

        # log.flush() # Removed
        # log.close() # Removed: File closing handled by logging.shutdown()
        logging.shutdown() # Add logging.shutdown()

if __name__ == '__main__':
    main()