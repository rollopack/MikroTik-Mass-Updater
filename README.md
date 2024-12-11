# MikroTik-Mass-Updater

This is a Python script to send commands to multiple Mikrotik devices via SSH. It shows improved, colored output (optional).

This script builds on work already done by Phillip Hutchison and Kevin Byrd. It has been improved with the following features:

*   **Output grouped by host:** Results are grouped by host, both on-screen and in the log file.
*   **Colored output (optional):** The on-screen output can be colored for better readability, highlighting hosts, commands, output, and errors. Colors can be turned on or off with the `--no-colors` command-line option.
*   **Log file:** The commands and their corresponding output are saved in a log file.
*   **Threading:** Uses threads for faster execution.
*   **Improved error handling:** The script now handles and reports errors in more detail.
*   **Connection with Paramiko:** The script uses the `Paramiko` library for SSH connections.

By default, the script checks for updates and installs them.
You can send any other commands you need.

## Requirements

*   **Python 3.6 or later**
*   **`paramiko` library:**

    ```bash
    pip install paramiko
    ```

## Notes

*   SSH access must be enabled on your Mikrotik devices.
*   The log file (`backuplog.txt`) is overwritten each time you run the script.
*   By default the script uses port 22. Use the `IP:port` format in the `list.txt` file only if your Mikrotik devices use a different SSH port.

## Options

*   `--no-colors`: Disables colored output on the screen.
*   `-u` or `--username`: Specifies the SSH username. **(Required)**
*   `-p` or `--password`: Specifies the SSH password. **(Required)**

## Usage

1. Download `mkmassupdate.py`
2. Install the `paramiko` library if it's not already installed
3. Edit or create the `list.txt` file with the IP addresses of your Mikrotik devices (one per line, in the format `IP` or `IP:port` if you are not using the default port 22).
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
192.168.1.10:2222
192.168.1.15
```
