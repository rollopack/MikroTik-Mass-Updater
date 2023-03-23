# MikroTik-Mass-Updater
Perl script to send commands to a Mikrotik list via SSH.
The script builds on work already done by Phillip Hutchison and Kevin Byrd.

By default the script looks for any updates and installs them.
Via $ssh->system you can pass any other command

## Note
- Access via ssh must be enabled on Mikrotik

## Usage
* Download mkmassupdate.pl and edit USERNAME and PASSWORD
* Edit or create list.txt
* ```perl mkmassupdate.pl```
