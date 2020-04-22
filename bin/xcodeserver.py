#!/usr/bin/env python3
#
#  MIT Licence
#
#  Copyright (c) 2020 Brice Rosenzweig.
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#  
#  The above copyright notice and this permission notice shall be included in all
#  copies or substantial portions of the Software.
#  
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#  SOFTWARE.
#  
#

import getpass
import argparse
import re
import json
import urllib3
from pprint import pprint,pformat
from requests import Session, Request
from requests_toolbelt import SSLAdapter

class Bot(object):
    def __init__(self,json):
        self.info = json

    def __str__(self):
        return 'Bot({},{})'.format( self.info['name'], self.info['_id'] )
    
    def __repr__(self):
        return 'Bot({},{})'.format( self.info['name'], self.info['_id'] )

    def id(self):
        return self.info['_id']

    def __repr__(self):
        return 'Integrations({},{})'.format( self.info['name'], self.info['_id'] )

class Integration(object):
    def __init__(self,json):
        self.info = json

    def number(self):
        return int(self.info['number'])

    def __repr__(self):
        return 'Integration({},{},{},{})'.format(self.info['_id'],self.info['endedTime'], self.info['number'], self.info['result'] )
    
class XcodeServer(object):
    def __init__(self,args):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.args = args
        self.config = { 'baseurl': 'https://localhost:20343' }
        self.session = Session()

    def construct_url(self,urldef):
        fullurldef = '{baseurl}'+urldef
        rv = fullurldef.format(baseurl=self.config['baseurl'])

        return rv

    def get(self,urldef,pydata=None):
        jsondata = json.dumps(pydata) if pydata else None
        self.last_response = self.session.get(self.construct_url(urldef),data=jsondata, verify=False)

    def all_bots(self):
        self.get('/api/bots' )
        return [Bot(x) for x in self.json()]
        
    def bot_for_name(self,name):
        self.get('/api/bots' )
        data = self.json()
        for bot in data:
            if name.lower() in bot['name'].lower():
                return Bot(bot)
        return None
    
    def integrations_for_bot(self,bot):
        self.get('/api/bots/{}/integrations'.format( bot.id() ) )
        return [Integration(x) for x in self.json() ]
                 
    def integration_for_bot_and_number(self,bot,num):
        integrations = self.integrations_for_bot(bot)
        
        for i in integrations:
            
            if i.number() == int(num):
                return i
            
        return None
    
    def json(self):
        return self.last_response.json()['results']
    
    def cmd_show(self):
        if len(self.args.args) > 0:
            name = self.args.args[0]
            bot = self.bot_for_name(name)

            if( len( self.args.args ) == 1):
                print( bot )
                print( bot.info.keys() )
            else:
                arg = self.args.args[1]
                try:
                   num = int(arg)
                   if int( num ) > 0:
                       integration = self.integration_for_bot_and_number(bot, int(num) )
                       print( integration)
                       if( len( self.args.args ) == 2 ):
                           print( integration.info.keys() )
                       else:
                           if self.args.args[2] in integration.info:
                               print( '{}: {}'.format( self.args.args[2], pformat(integration.info[self.args.args[2]],indent=2) ) )
                           else:
                               print( 'key {} not in {}'.format( self.args.args[2], integration.info.keys() ) )
                except:
                    if arg in bot.info.keys():
                        print( '{}: {}'.format( arg, pformat(bot.info[arg],indent=2 ) ) )
                    else:
                        print( 'key {} not integration number or bot key {}'.format( arg, bot.info.keys() ) )

        else:
            data = self.all_bots()
            for bot in data:
                print( bot )
            

    def cmd_list_integrations(self):
        name = self.args.args[0]
        bot = self.bot_for_name(name)
        integrations = self.integrations_for_bot(bot )
        for integration in integrations:
            print( integration)
        
    def cmd_list_bots(self):
        data = self.all_bots()
        for bot in data:
            print( bot )

if __name__ == "__main__":
    commands = {
        'bots': {'attr':'cmd_list_bots','help':'List bots' },
        'integrations': {'attr':'cmd_list_integrations','help':'List integrations' },
        'show': {'attr':'cmd_show','help':'Show integrations' },
    }
    description = "\n".join( [ '  {}: {}'.format( k,v['help'] ) for (k,v) in commands.items() ] )
    parser = argparse.ArgumentParser( description="Interact with the xcode server using:\n", formatter_class=argparse.RawTextHelpFormatter )
    parser.add_argument( 'command', metavar='Command', help='command is devices, clients\n' + description )
    parser.add_argument( 'args', metavar='Arguments', nargs='*' )

    args = parser.parse_args()

    process = XcodeServer(args)
    if args.command in commands:
        getattr(process,commands[args.command]['attr'])()
    else:
        print( 'Invalid command "{}"'.format( args.command) )
        parser.print_help()
