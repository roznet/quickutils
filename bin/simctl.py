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

import sys
import subprocess
import os
import time
from distutils import version
import hashlib
import argparse
import json
from pprint import pprint
import shutil
import fnmatch
from collections import defaultdict

try:
    from fuzzywuzzy import fuzz
except:
    class fuzz:
        def ratio( s1, s2 ):
            if s1 in s2:
                return 1
            return 0

class SimFilter:
    def __init__(self,args):
        self.args = args


    def valid(self,runtime=None,device=None):
        rv = True
        if runtime:
            if self.args.system and self.args.system.lower() not in runtime['name'].lower():
                rv = False
        if device:
            if self.args.name and self.args.name.lower() not in device['name'].lower():
                rv = False
            if self.args.booted and device['state'].lower() != 'booted':
                rv = False
        return rv


class SimData:
    def __init__(self,args,filter):
        self.args = args
        self.verbose = args.verbose
        cmd = ['xcrun', 'simctl', 'list', '-j' ]
        if self.verbose:
            print( f'Running {cmd}' )
        out = subprocess.Popen( cmd, stdout= subprocess.PIPE, stderr=subprocess.STDOUT )
        (list,out) = out.communicate()
        self.simdata = json.loads( list.decode('utf-8') )
        if self.verbose:
            k = len(self.simdata['devices'])
            print( f'Found {k} devices' )
        self.filter = filter

    def list_containers(self,device):
        rv = dict()
        findpath = os.path.join(device['dataPath'], 'Containers' )
        if os.path.isdir( findpath ):
            cmd =['find', findpath, '-name', '.simneedle.*']
            if self.verbose:
                print( f'Running {cmd}' )
            out = subprocess.Popen( cmd, stdout= subprocess.PIPE, stderr=subprocess.STDOUT )
            (found,out) = out.communicate()
            paths = found.decode('utf-8').split('\n' )
            for path in paths:
                if path:
                    bundle = os.path.basename( path )[ len('.simneedle.'): ]
                    mtime = time.localtime(os.path.getmtime(path))

                    documentpath = os.path.dirname( path )
                    containerpath = os.path.dirname( documentpath )
                    if bundle:
                        rv[bundle] = {'device': device, 'bundle':bundle,'mtime':mtime,'path':containerpath,'files':len( os.listdir( documentpath ) ) }
        return rv
        
        
    def get_app_container(self,device,app,which='data'):
        found = None
        if device['isAvailable'] and device['state'] != 'Shutdown':
            cmd = ['xcrun', 'simctl', 'get_app_container', device['udid'], app, which]
            if self.verbose:
                print( f'cRunning {cmd}' )
            try:
                out = subprocess.Popen( cmd , stdout= subprocess.PIPE, stderr=subprocess.STDOUT )
                (path,out) = out.communicate()
                found = path.decode('utf-8').rstrip()
            except:
                found = None
        if not found:
            findpath = os.path.join(device['dataPath'],'Containers')
            if os.path.isdir( findpath ):
                cmd =['find', findpath, '-name', f'.simneedle.{app}']
                if self.verbose:
                    print( f'Running {cmd}' )
                out = subprocess.Popen( cmd, stdout= subprocess.PIPE, stderr=subprocess.STDOUT )
                (path,out) = out.communicate()
                found = path.decode('utf-8').rstrip()
                suffixlength = len(f'/Documents/.simneedle.{app}')
                found = found[:-suffixlength]
        return found
            
    def getruntime(self,identifier):
        for runtime in self.simdata['runtimes']:
            if runtime['identifier'] == identifier:
                return runtime
        clean = identifier.replace( 'com.apple.CoreSimulator.SimRuntime.', '' )
        split = clean.split( '-' )
        system = split[0]
        version = '.'.join( split[1:] )
        return { 'identifier': identifier, 'name': f'{system} {version}', 'version': version }


    def list(self,searchname=None,searchruntime=None):
        to_sort = []
        
        for (runtimeid,devices) in self.simdata['devices'].items():
            runtime = self.getruntime( runtimeid )
            for device in devices:
                if self.filter.valid( runtime, device ):
                    to_sort.append( (runtime,device) )
        if self.args.group[0] == 'v':
            to_sort.sort( key = lambda x: version.LooseVersion( x[0]['version'] ) )
        else:
            to_sort.sort( key = lambda x: (x[1]['name'], version.LooseVersion( x[0]['version'] ) ) )
        for (runtime,device) in to_sort:
            print( '{}: {} {}'.format(runtime['name'], device['name'],'[Booted]' if device['state'] == 'Booted' else '' ) )

    def sorted_list(self,searchname,searchruntime):
        rv = []
        for (runtimeid,devices) in self.simdata['devices'].items():
            runtime = self.getruntime( runtimeid )
            for device in devices:
                device['runtime'] = runtime
                if searchname:
                    device['searchnameratio'] = fuzz.ratio(searchname.lower(), device['name'].lower() )
                else:
                    device['searchnameratio'] = 0
                if searchruntime:
                    device['runtimeratio'] = fuzz.ratio(searchruntime.lower(), runtime['name'].lower() )
                else:
                    device['runtimeratio'] = 0
                rv.append( device )
                
        rv.sort( key=lambda k: (k['searchnameratio'], k['runtimeratio'], 1 if k['state'] == 'Booted' else 0, k['runtime']['version']), reverse=True )
        return rv


    def version_by_name(self):
        rv = defaultdict(list)
        for (runtimeid,devices) in self.simdata['devices'].items():
            runtime = self.getruntime( runtimeid )
            for device in devices:
                device['runtime'] = runtime
                rv[ device['name'] ].append( device )
        for (name,devices) in rv.items():
            devices.sort( key=lambda x: version.LooseVersion( x['runtime']['version'] ) )
        return rv
        
    
    def find(self,searchname,searchruntime):
        exactmatch = None
        fuzzymatch = []
        for (runtime,devices) in self.simdata['devices'].items():
            runtime = self.getruntime(runtime)
            if runtime['name'].lower() == searchruntime.lower():
                for device in devices:
                    if self.filter.valid( runtime, device ):
                        print( '  {} {}'.format(device['name'],device['udid'] ) )
                        return device


class Driver:
    def __init__(self,args):
        self.args = args
        self.simdata = SimData(args,SimFilter(args))
        
    def cmd_list(self):
        self.simdata.list(self.args.name,self.args.system)
        
    def cmd_find(self):
        l = self.simdata.sorted_list(self.args.name,self.args.system)
        first = '>'
        for device in l[:5]:
            print( '{} {}: {} {}'.format(first,device['runtime']['name'], device['name'], 'Booted' if device['state'] == 'Booted' else '' ) )
            first = ' '

    def cmd_info(self):
        devices = self.simdata.sorted_list(self.args.name, self.args.system )
        count = int(self.args.count) if self.args.count else 1
        pprint( devices[:count] )

    def cmd_upgrade(self):
        l = self.simdata.version_by_name()
        count = int(self.args.count) if self.args.count else 2

        # name(iphone 11, ipad, ...), devices: array of all devices version for that name (ios14, ios13.2, ...)
        for (name,devices) in l.items():
            if len(devices):
                if self.args.name and name != self.args.name:
                    continue
                bundles = defaultdict(list)
                # find all application bundle data directory for device
                for device in devices:
                    containers = self.simdata.list_containers( device )
                    for bundle,info in containers.items():
                        bundles[bundle].append( info )
                if len(bundles):
                    headerprinted = False
                    for bundle,infos in bundles.items():
                        if len(infos) < 2:
                            continue
                        if self.args.app:
                            if bundle not in self.args.app:
                                continue

                        summaries = []
                        if not headerprinted:
                            print( f'{name} has app to upgrade' )
                            headerprinted = True

                        print( '  {}:'.format(bundle) )
                        # if more than one device, look for source
                        if len(infos) > 1:
                            foundavailable = False
                            for info in reversed(infos):
                                if not foundavailable:
                                    if info['device']['isAvailable']:
                                        foundavailable = True
                                else:
                                    info['source'] = True
                                    break

                        useinfos = infos

                        pathfrom = None
                        pathto   = None
                        if self.args.count:
                            useinfos = infos[ - int(self.args.count) :]
                        for info in useinfos:
                            prefix = ' '
                            if info['device']['isAvailable']:
                                pathto = info['path']
                                prefix = '>'
                            if 'source' in info:
                                pathfrom = info['path']
                                prefix = '*'
                            one = '{}{}[{}: {}]'.format( prefix, info['device']['runtime']['name'], time.strftime('%b %Y', info['mtime']), info['files'] )
                            summaries.append( one )
                            if self.args.path:
                                print( '    {:26} : {}'.format( one, info['path'] ) )
                            else:
                                print( '    {:26} '.format( one ) )
                                              
                        if pathfrom and pathto:
                            print( 'Command to execute:' )
                            print()
                            print( f"find {pathfrom}/Documents -type f -not -name '.*' | xargs -J % mv -f % {pathto}/Documents" )
                            print()
                                              
                            
        
    def cmd_dir(self):
        if len(self.args.app) < 1:
            print( 'No app identifier provided' )
        else:
            devices = self.simdata.sorted_list(self.args.name, self.args.system )
            if len(devices):
                found = self.simdata.get_app_container(devices[0],self.args.app[0])
                print( found )
    
if __name__ == "__main__":
                
    commands = {
        'list':{'attr':'cmd_list','help':'List all devices found'},
        'info':{'attr':'cmd_info','help':'List info on devices'},
        'find':{'attr':'cmd_find','help':'find device'},
        'dir':{'attr':'cmd_dir','help':'dir for top device'},
        'upgrade':{'attr':'cmd_upgrade','help':'upgrade app containers'},
    }

    description = "\n".join( [ '  {}: {}'.format( k,v['help'] ) for (k,v) in commands.items() ] )

    parser = argparse.ArgumentParser( description='Wrapper around xcrun simctl', formatter_class=argparse.RawTextHelpFormatter )
    parser.add_argument( 'command', metavar='Command', help='command to execute:\n' + description)
    parser.add_argument( '-g', '--group', help='Group list by [device|version]', default='version' )
    parser.add_argument( '-b', '--booted', action='store_true', help='only booted' )
    parser.add_argument( '-v', '--verbose', action='store_true', help='verbose' )
    parser.add_argument( '-s', '--system', help='system runtime to use as filter [ios 13.5, ..]' )
    parser.add_argument( '-n', '--name',  help='simulator name string to use as filter [iPhone 11, ...]' )
    parser.add_argument( '-c', '--count',  help='number of device to display' )
    parser.add_argument( '-p', '--path',  action='store_true', help='display paths in output' )
    parser.add_argument( 'app',    metavar='app', nargs='*', default='', help='app identifier' )
    args = parser.parse_args()

    command = Driver(args)

    if args.command in commands:
        getattr(command,commands[args.command]['attr'])()
    else:
        print( 'Invalid command "{}"'.format( args.command) )
        parser.print_help()

