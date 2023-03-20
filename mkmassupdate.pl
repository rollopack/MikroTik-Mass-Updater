#!/usr/bin/perl
################################################### 
#         MikroTik Mass Updater v1.0
#         Original Written by: Phillip Hutchison
#         Revamped version by: Kevin Byrd 
#         Ported to OpenSSH by: Gabriel Rolland
###################################################

#This Script needs the following Perl Modules installed on the machine that it is run on.
use Net::OpenSSH;
$count = 1;

use constant USERNAME => 'admin';
use constant PASSWORD => 'password';

# Name of the list file containing the IP addresses and Names of the mikrotiks.
# List file format is xxx.xxx.xxx.xxx 
use constant IP_LIST_FILE => 'list.txt';

# Directory where the log will be stored: /home/kevin or ./Dropbox for dropbox
use constant DIR => '/mnt/dropbox/Documenti/Mikrotik/Mikrotik massupdater/';

# Log filename
use constant LOG_FILE => 'backuplog.txt';

open(FH, IP_LIST_FILE) or die "Can't open the list file $!\n";
open(LOG, ">>", LOG_FILE) or print "Couldn't open log file: $!";

# DEBUG
# $Net::OpenSSH::debug=-1;

while($line = <FH>)
{
      chomp($line);
      ($IP, $port) = split(':', $line);

      my $ssh = Net::OpenSSH->new($IP, user => USERNAME, password => PASSWORD, port  => 22, timeout => 3, kill_ssh_on_timeout => 1, master_opts => [-o => "StrictHostKeyChecking=no"]);
      $count++;
      
      print "Connecting to ".$IP."\n";
      print LOG "Connecting to $IP\n";

      if($ssh->error){
         print "Couldn't establish SSH connection: ". $ssh->error;
         print LOG "Couldn't establish SSH connection: ". $ssh->error."\n";
         print LOG $ssh->error."\n";
      }else{         
         $ssh->system('/system identity print');
         $ssh->system('/system package update check-for-updates');
         $ssh->system('/system package update install');
         #$ssh->system('/interface sstp-client set max-mtu=1400 0');
         #$ssh->system('/interface pptp-client set max-mtu=1400 0');
         $ssh->disconnect;
         print "\n";
      }
}

close(FH);
close(LOG);
