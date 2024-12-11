# MikroTik-Mass-Updater

This is a Python script to send commands to multiple Mikrotik devices via SSH. It shows improved, colored output (optional).

This script builds on work already done by Phillip Hutchison and Kevin Byrd. It has been improved with the following features:

*   **Output grouped by host:** Results are grouped by host, both on-screen and in the log file.
*   **Colored output (optional):** The on-screen output can be colored for better readability, highlighting hosts, commands, output, and errors. Colors can be turned on or off with the `--no-colors` command-line option.
*   **Log file: ** The commands and their corresponding output are saved in a log file.
*   **Threading:** Uses threads for faster execution.
*   **Improved error handling:** The script now handles and reports errors in more detail.

By default, the script checks for updates and installs them.
You can send any other commands you need.

## Requirements

*   **Python 3.6 or later**
*   **`paramiko` library**

## Notes

*   SSH access must be enabled on your Mikrotik devices.
*   The log file (`backuplog.txt`) is overwritten each time you run the script.
*   By default the script uses port 22, otherwise you can specify the desired port `IP:port`

## Usage

1. Download `mkmassupdate.py`
2. Install the `paramiko` library if it's not already installed
3. Edit `USERNAME` and `PASSWORD` in the script with your Mikrotik login credentials.
4. Edit or create the `list.txt` file with the IP addresses of your Mikrotik devices (one per line, in the format `IP` or `IP:port` if you are not using the default port 22).
5. Run the script:

    *   **With colors (default):**

        ```bash
        python mkmassupdate.py
        ```

    *   **Without colors:**

        ```bash
        python mkmassupdate.py --no-colors
        ```

## Options

*   `--no-colors`: Disables colored output on the screen.

## Example `list.txt` file
192.168.1.1

192.168.1.2

192.168.1.10:2222

192.168.1.15
