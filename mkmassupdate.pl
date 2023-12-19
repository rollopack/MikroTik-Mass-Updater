#!/usr/bin/perl

###################################################
#         MikroTik Mass Updater v2.1
#         Original Written by: Phillip Hutchison
#         Revamped version by: Kevin Byrd 
#         Ported to OpenSSH by: Gabriel Rolland
###################################################

use strict;
use warnings;
use Net::OpenSSH::Parallel;

use constant USERNAME => 'admin';
use constant PASSWORD => 'password';
use constant IP_LIST_FILE => 'list.txt';
use constant LOG_FILE => 'backuplog.txt';

open(FH, IP_LIST_FILE) or die "Can't open the list file $!\n";
open(LOG, ">>", LOG_FILE) or print "Couldn't open log file: $!";

my $pssh = Net::OpenSSH::Parallel->new();

while (my $line = <FH>) {
   chomp($line);
   my ($IP, $port) = split(':', $line);

   $pssh->add_host($IP, user => USERNAME, password => PASSWORD, port => 22, timeout => 3, kill_ssh_on_timeout => 1, master_opts => [-o => "StrictHostKeyChecking=no"]);

   $pssh->push($IP, command => '/system identity print');
   $pssh->push($IP, command => '/system package update check-for-updates');
   $pssh->push($IP, command => '/system package update install');
   $pssh->push($IP, command => 'quit');
}

if (!$pssh->run) {
   print "Error \n";
}

$pssh->run;

close(FH);
close(LOG);
