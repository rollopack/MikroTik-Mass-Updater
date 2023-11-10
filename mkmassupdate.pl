#!/usr/bin/perl

###################################################
#         MikroTik Mass Updater v2.0
#         Original Written by: Phillip Hutchison
#         Revamped version by: Kevin Byrd 
#         Ported to OpenSSH by: Gabriel Rolland
###################################################

use strict;
use warnings;
use Net::OpenSSH;
use Parallel::ForkManager;
use constant USERNAME => 'admin';
use constant PASSWORD => 'password';
use constant IP_LIST_FILE => 'list.txt';
use constant LOG_FILE => 'backuplog.txt';

open(FH, IP_LIST_FILE) or die "Can't open the list file $!\n";
open(LOG, ">>", LOG_FILE) or print "Couldn't open log file: $!";

my $pm = Parallel::ForkManager->new(5);  # Set the number of parallel processes

while (my $line = <FH>) {
    $pm->start and next;

    chomp($line);
    my ($IP, $port) = split(':', $line);

    my $ssh = Net::OpenSSH->new("$IP", user => USERNAME, password => PASSWORD, port  => 22, timeout => 3, kill_ssh_on_timeout => 1, master_opts => [-o => "StrictHostKeyChecking=no"]);

    print "Connecting to ".$IP."\n";
    print LOG "Connecting to $IP\n";

    if ($ssh->error) {
        print "Couldn't establish SSH connection: ". $ssh->error;
        print LOG "Couldn't establish SSH connection: ". $ssh->error."\n";
        print LOG $ssh->error."\n";
    } else {         
        $ssh->system('/system identity print');
        $ssh->system('/system package update check-for-updates');
        $ssh->system('/system package update install');
        #$ssh->system('/interface sstp-client set max-mtu=1400 0');
        #$ssh->system('/interface pptp-client set max-mtu=1400 0');
        $ssh->disconnect;
        print "\n";
    }

    $pm->finish;
}

$pm->wait_all_children;
close(FH);
close(LOG);
