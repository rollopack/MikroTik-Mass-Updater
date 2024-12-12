# MikroTik Mass Updater

This is a Python script to send commands to multiple Mikrotik devices via the API. It provides colored output (optional) and detailed logging.

This script builds on work already done by Phillip Hutchison and Kevin Byrd, ported to Python and the Mikrotik API by Gabriel Rolland.

**Key Features:**

*   **MikroTik API:** Uses the `librouteros` library to interact with the Mikrotik API, which is generally more efficient and provides more functionality than SSH.
*   **Concurrent operation:** Uses threads to connect to multiple devices simultaneously, significantly reducing the execution time.
*   **Output grouped by host:** Results are clearly grouped by host, both on-screen and in the log file.
*   **Colored output (optional):** The on-screen output can be colored for better readability, highlighting hosts, commands, output, and errors. Colors can be turned on or off with the `--no-colors` command-line option.
*   **Detailed logging:** Logs all commands sent, output received, and errors encountered to a log file (`backuplog.txt`).
*   **Error handling:** The script handles connection errors, API errors, and other exceptions gracefully.
*   **Idempotent Update Logic**: Attempts to install updates regardless of the current status. Includes cases where status may be unclear or device already updated.


By default, the script checks for updates and installs them if the router does not refuse the operation.
You can adapt the script to send any other commands you need.

## Requirements

*   **Python 3.6 or later**
*   **`librouteros` library:**

    ```bash
    pip install librouteros
    ```
    or
    ```bash
    sudo apt install python3-librouteros
    ```

## Notes

*   API access must be enabled on your Mikrotik devices (usually on port 8728).
*   The log file (`backuplog.txt`) is overwritten each time you run the script.
*   By default the script uses port 8728, otherwise you can specify the desired port `IP:port`

## Options

*   `--no-colors`: Disables colored output on the screen.
*   `-u` or `--username`: Specifies the API username. **(Required)**
*   `-p` or `--password`: Specifies the API password. **(Required)**

## Usage

1. Download `mkmassupdate.py`
2. Install the `librouteros` library if it's not already installed
3. Edit or create the `list.txt` file with the IP addresses of your Mikrotik devices (one per line, in the format `IP` or `IP:port` if you are not using the default port 8728).
4. Run the script, providing your username and password as arguments:

    *   **With colors (default):**

        ```bash
        python mkmassupdate.py -u your_username -p your_password
        ```

    *   **Without colors:**

        ```bash
        python mkmassupdate.py --no-colors -u your_username -p your_password
        ```

    Replace `your_username` and `your_password` with your actual credentials.

## Example `list.txt` file

```
192.168.1.1
192.168.1.2
192.168.1.10:8729
192.168.1.15
```
