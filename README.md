# MikroTik Mass Updater
This is a Python script to send commands to multiple Mikrotik devices via the API. It provides concurrent operations, detailed logging (with optional console colors), a job summary, and configurable settings.

This script builds on work already done by Phillip Hutchison and Kevin Byrd, ported to Python and the Mikrotik API by Gabriel Rolland.

**Key Features:**

*   **MikroTik API:** Uses the `librouteros` library to interact with the Mikrotik API.
*   **Concurrent Operation:** Employs threading to connect to multiple devices simultaneously. The number of threads is configurable (`--threads`).
*   **Structured Logging:** Uses Python's standard `logging` module.
    *   Detailed logs are saved to a file in the `log` directory. Each run of the script generates a new log file with a timestamp in its name (e.g., `mkmassupdate-2023-10-27-10-30-00.log`). File logs include timestamps, log levels, and thread names.
    *   Console output is formatted for readability, with optional color-coding for different log levels (`--no-colors` to disable).
    *   Debug mode for more verbose logging (`--debug`).
*   **Job Summary:** At the end of execution, a summary is provided detailing total hosts processed, successful operations, and failed operations (including a list of specific failed IPs). "Unknown" host failures are counted in totals but not itemized in the failed IP list.
*   **Flexible Host Configuration:**
    *   IP list sourced from a file (default: `list.txt`, configurable via `--ip-list`).
    *   Supports `IP`, `IP:PORT`, and `IP[:PORT]|USERNAME|PASSWORD` formats in the list file.
    *   Default API port is 8728, configurable via `--port`.
*   **Error Handling:** Graceful handling of connection errors, API errors, and other exceptions, with retries for command execution. Malformed lines in the IP list are skipped with a warning.
*   **Update Logic:** Checks for and installs updates by default.
    *   `--dry-run` mode to simulate without actual installation.
    *   Configurable attempts and delay for update status checking (`--update-check-attempts`, `--update-check-delay`).
*   **Custom Commands:** Supports execution of user-defined custom commands on all devices.
*   **Graceful Shutdown:** Handles `KeyboardInterrupt` (Ctrl+C) cleanly, attempting to stop operations and finalize.
*   **Start Line:** Option to start processing the IP list from a specific line number (`--start-line`).

## Requirements

*   **Python 3.6 or later**
*   **`librouteros` library:** (Tested with v3.4.1, other versions might work)

    ```bash
    pip install librouteros
    ```

    or on Debian/Ubuntu:

    ```bash
    sudo apt install python3-librouteros
    ```

## Notes

*   API access (port 8728 by default, or custom with `--port` or `IP:PORT` syntax) must be enabled on your Mikrotik devices.
*   The log file is overwritten each time the script is run.
*   Default connection timeout is 15 seconds (change with `--timeout`).

## Options

*   `-u USERNAME`, `--username USERNAME`: Specifies the API username. **(Required)**
*   `-p PASSWORD`, `--password PASSWORD`: Specifies the API password. **(Required)**
*   `-t THREADS`, `--threads THREADS`: Number of concurrent threads to use. Default: `5`.
*   `--timeout TIMEOUT`: Connection timeout in seconds for API communication. Default: `5`.
*   `--ip-list FILE_PATH`: Path to the IP list file. Default: `list.txt`.
*   `--port API_PORT`: Default API port if not specified in the IP list file. Default: `8728`.
*   `--update-check-attempts ATTEMPTS`: Number of attempts to check update status. Default: `15`.
*   `--update-check-delay DELAY`: Delay (seconds) between update status checks. Default: `2.0`.
*   `--no-colors`: Disables colored output on the console.
*   `--dry-run`: Enables dry-run mode (simulates updates but doesn't install).
*   `--start-line LINE_NUM`: Start from this line number in the IP list file (1-based). Default: `1`.
*   `--debug`: Enables debug logging level for more verbose output.
*   `--cloud-password PASSWORD`: Password for cloud backup. **(Required for performing cloud backup)**
*   `--upgrade-firmware`: Perform firmware upgrade.

## Usage

1.  Download or clone `mkmassupdate.py`.
2.  Install the `librouteros` library.
3.  Prepare your IP list file (default `list.txt`).
4.  (Optional) Edit the `custom_commands` list within `mkmassupdate.py` for custom operations.
5.  Run the script with your credentials and desired options:

    ```bash
    python3 mkmassupdate.py -u your_username -p your_password [OPTIONS]
    ```

    **Examples:**

    *   **Basic run:**
        ```bash
        python3 mkmassupdate.py -u admin -p pass123
        ```

    *   **Using a custom IP list and 20 threads:**
        ```bash
        python3 mkmassupdate.py -u admin -p pass123 --ip-list /path/to/my_routers.txt -t 20
        ```

    *   **Dry run with increased timeout and debug logging:**
        ```bash
        python3 mkmassupdate.py -u admin -p pass123 --dry-run --timeout 30 --debug
        ```

    *   **Perform cloud backup with a specified password:**
        ```bash
        python3 mkmassupdate.py -u admin -p pass123 --cloud-password your_cloud_backup_password
        ```
    *   **Perform firmware upgrade:**
        ```bash
        python3 mkmassupdate.py -u admin -p pass123 --upgrade-firmware
        ```

## Custom Commands Format

The `custom_commands` list in the script supports two formats:

1.  **Simple commands** (string):
    ```python
    '/interface/print'
    ```
2.  **Commands with parameters** (tuple: command string, parameters dictionary):
    ```python
    ('/user/add', {'name': 'newuser', 'password': 'userpass', 'group': 'read'})
    ```

**Example `custom_commands` list:**

```python
custom_commands = [
'/system/clock/print',
('/interface/set', {
	'numbers': 'ether1',  # Assuming 'numbers' is how you identify the interface
	'comment': 'Main WAN Link'
})
]
```
Note: Parameter names must match MikroTik API specifications.

### IP List File Format (list.txt or custom)

One entry per line. Supported formats:

*   **IP only** (uses default API port and script credentials)
    ```
    192.168.1.1
    ```

*   **IP with custom port**
    ```
    192.168.1.2:8729
    ```

*   **IP[:port] with custom credentials** (username|password)
    ```
    192.168.1.3|customuser|custompass
    192.168.1.4:8729|customuser2|custompass2
    ```

*   **Lines starting with # are comments. Empty lines are ignored.**


## Screenshot
![ScreenShot](./screenshot.png)

## Disclaimer

This script is provided as-is, without warranty of any kind. Use it at your own risk. Always test thoroughly in a non-production environment before deploying to production devices.
