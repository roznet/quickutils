#!/usr/bin/env python3

from zeroconf import ServiceBrowser, Zeroconf
import argparse
from zeroconf import ZeroconfServiceTypes
import time
import socket

class Listener:

    def remove_service(self, zeroconf, type, name):
        print( f'Service {name} removed' )

    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        if info:
            self.ip = socket.inet_ntoa(info.addresses[0]) if info.addresses else 'N/A'
            self.port = info.port
            self.info = info
            print( f'Found Service {info.name} provided by {info.server} at {self.ip}:{self.port}')


class Driver:
    def __init__(self,args):
        self.args = args
        
    def cmd_list(self):
        print( '\n'.join(ZeroconfServiceTypes.find() ) )

    def cmd_show(self):
        zeroconf = Zeroconf()
        listener = Listener()
        for service in self.args.services:
            print( f'Adding listener for {service}' )
            browser = ServiceBrowser(zeroconf, service, listener)
            while( True ):
                time.sleep(1.0)

        

        
if __name__ == "__main__":
                
    commands = {
        'list':{'attr':'cmd_list','help':'list available services'},
        'show':{'attr':'cmd_show','help':'show details for one service '},
    }
    
    description = "\n".join( [ '  {}: {}'.format( k,v['help'] ) for (k,v) in commands.items() ] )
    
    parser = argparse.ArgumentParser( description='Remote Copy', formatter_class=argparse.RawTextHelpFormatter )
    parser.add_argument( 'command', metavar='Command', help='command to execute:\n' + description)
    parser.add_argument( '-v', '--verbose', action='store_true', help='verbose output' )
    parser.add_argument( 'services',    metavar='SERVICES', nargs='*' )
    args = parser.parse_args()

    command = Driver(args)

    if args.command in commands:
        getattr(command,commands[args.command]['attr'])()
    else:
        print( 'Invalid command "{}"'.format( args.command) )
        parser.print_help()
