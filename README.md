# MikroTik mass update
Perl script to send commands to a list of Mikrotik devices via SSH.

The script is based on work already done by Phillip Hutchison and Kevin Byrd.

By default the script checks for any updates and installs them.
Via $ssh->system you can pass any other command

## Note
- Access via ssh must be enabled on Mikrotik

## Usage
* Download mkmassupdate.pl and change USERNAME and PASSWORD
* Edit or create list.txt
* ```perl mkmassupdate.pl```
