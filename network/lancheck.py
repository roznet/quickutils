#!/usr/bin/python
#
# 
# 
#

import csv
import os
import datetime
import json
import argparse
import xml.etree.ElementTree as ET
import pprint
import subprocess
from socket import inet_aton
import re
from collections import defaultdict

def json_serial_defaults(obj):
    '''
    default serialisatoin for json
    '''
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()

    if isinstance(obj, Device):
        return obj.info

    raise TypeError( "Type %s not serializable" %(type(obj),))

def mac_vendor(mac):
    prefixes = '/usr/local/share/nmap/nmap-mac-prefixes'
    if not os.path.isfile( prefixes ):
        prefixes = '/usr/share/nmap/nmap-mac-prefixes'

    macpref = mac.upper().replace(':', '')[:6]

    found = None
    if os.path.isfile( prefixes ):
        with open( prefixes, 'r') as fp:
            line = fp.readline()
            while( line ):
                if line.startswith(macpref):
                    found = line[7:].rstrip()
                    break
                line = fp.readline()

    return found
    
class Device:
    def __init__(self,info):
        self.info = dict(info)
        self.changed = {}
        if 'firstseen' in self.info and isinstance(self.info['firstseen'], str):
            self.info['firstseen'] = datetime.datetime.strptime(self.info['firstseen'], '%Y-%m-%dT%H:%M:%S.%f')
            
    def __getitem__(self,key):
        rv = None
        if key in self.info:
            rv = self.info[key]

        if key == 'vendor' and self.mac():
            if rv == None:
                rv = mac_vendor( self.mac() )
            
        return rv

    def __setitem__(self,key,value):
        if key in self.info and self.info[key] != value:
            # only record first change
            if key not in self.changed:
                self.changed[key] = self.info[key]
        self.info[key] = value

    def __delitem__(self,key):
        del self.info[key]
        
    def __repr__(self):
        return 'Device(%s)' %(self.info,)

    def __str__(self):
        return 'Device%s(%d keys)' %( [self.info[x] if x in self.info else '' for x in ( 'mac', 'ipv4', 'name', 'vendor') ], len(self.info),  )

    def __contains__(self,key):
        return key in self.info
    
    def mac(self):
        return self.info['mac'] if 'mac' in self.info else None

    def matches(self,key):
        for v in self.info.values():
            if key in str(v):
                return True

        return False


    def is_disabled(self):
        return self.info['ipv4'] == '0.0.0.0'

    
    def record_as_new(self):
        self.changed = dict(self.info)

    def clear_changes(self):
        self.changed = {}
        
    def has_changed(self,check=None):
        if check:
            for key in check:
                if key in self.changed:
                    return True
        else:
                
            return len(self.changed) > 0

    def is_new(self):
        return self.changed == self.info

    def add_vendor(self):
        pre = self.info['vendor'] if 'vendor' in self.info else None
        if not pre:
            vendor = mac_vendor(self.mac())
            if vendor:
                self.info['vendor'] = vendor
    
    def show_changes(self,check=None):
        for key in self.changed:
            if not check or key in check:
                if self.changed[key] == self.info[key]:
                    print 'new %s: %s' %( key, self.info[key] )
                else:
                    print 'dif %s: %s -> %s' %( key, self.changed[key], self.info[key] )
    
class DeviceList:
    def __init__(self,devices):
        '''
        contructor with list of Device object
        '''
        self.devices_by_mac = {}
        for device in devices:
            mac = device.mac()
            if mac:
                if mac in self.devices_by_mac:
                    print 'Duplicate %s %s' %(device, self.devices_by_mac[mac],)
                else:
                    self.devices_by_mac[ mac ] = device
                                              
        self.cols = None
        self.changed = 0
        self.added = 0

    def __repr__(self):
        return 'DeviceList(%s)'  %( self.devices_list() , )
    
    def __str__(self):
        return 'DeviceList( %d devices, [\n%s\n]' %( len(self.devices_by_mac), '\n'.join([str(x) for x in self.devices_list()]))

    def __len__(self):
        return len(self.devices_by_mac)
    
    def __getitem__(self,key):
        if isinstance(key, int):
            return self.devices_by_mac.values()[key]
        elif isinstance(key, str):
            return self.devices_by_mac[key]

        return None

    def __contains__(self,key):
        if isinstance(key, int):
            return key < len(self.devices_by_mac)
        elif isinstance(key, str):
            return key in self.devices_by_mac

    def devices_list(self):
        return self.devices_by_mac.values()

    def devices_list_ordered_by(self,sortfield):
        values = self.devices_by_mac.values()
        if sortfield == 'mac':
            sortkey = lambda x: (x[sortfield])
        else:
            sortkey = lambda x: (x[sortfield],x['mac'])
            if sortfield == 'ipv4':
                sortkey = lambda x: (inet_aton(x[sortfield]),x['mac'])
                
        sortedvalues = sorted( values, key=sortkey )
        return sortedvalues

    def unknown_devices(self):
        rv = []
        for mac,dev in self.devices_by_mac.iteritems():
            if 'name' not in dev or dev['name'] == '':
                rv += [dev]
                
        return DeviceList( rv )

    def clear_changes(self):
        self.changed = 0
        self.added = 0
        for one in self.devices_by_mac.values():
            one.clear_changes()
    
    def has_changes(self):
        return self.changed > 0 or self.added > 0
    
    def status(self):
        return 'DeviceList(total=%d,changed=%d,added=%d)' %(len(self.devices_by_mac),self.changed, self.added)
    
    @staticmethod
    def from_json(fname,all=True):
            
        devices = []
        if os.path.isfile(fname):
            with open( fname, 'r') as jsonfile:
                initial = json.load( jsonfile )
                for one in initial:
                    device = Device( one )
                    if all or not device.is_disabled():
                        devices.append( device )

        return DeviceList( devices )

    @staticmethod
    def from_csv_file(fname):
        prev = {}

        with open( fname, 'r' ) as csvfile:
            reader = csv.reader( csvfile, delimiter=';')
            header = reader.next()
            header = ['name','ipv4','vendor','mac','ignore1','ignore2']
            for row in reader:
                one = Device(dict( zip(header,row) ))
                mac = one['mac'].upper() 
                prev[mac] = Device({'mac':mac})
                for key in ['name','ipv4','vendor']:
                    prev[mac][key] = one[key]
        return DeviceList(prev)
        

    @staticmethod
    def from_nmap_xml_file(fname):
        '''
        take the output of sudo nmap -sn -oX list.xml 192.168.1.0/24 and create new array of dict
        will merge previous information if existing is provided
        '''

        tree = ET.parse(fname)
        root = tree.getroot()

        all = []
        done = {}

        for host in root.findall('host'):
            found = Device({ 'firstseen':datetime.datetime.now()})
            for addr in host.findall('address'):
                found[ addr.attrib['addrtype'] ] = addr.attrib['addr']
                if 'vendor' in addr.attrib:
                    found['vendor']=addr.attrib['vendor']

            for name in host.iter('hostname'):
                found['hostname'] = name.attrib['name']

            if found.mac() and found.mac() not in done:
                done[found.mac()] = 1
                all.append(found)

        return DeviceList(all)

    @staticmethod
    def from_wifi_json(fname):
        rv = {}
        with open( fname ) as fp:
            prev = json.load( fp )
            for network,defs in prev.iteritems():
                for one in defs:
                    device = {'network':network}
                    wifis = None
                    for key,val in one.iteritems():
                        if key == 'wifi':
                            wifis = val
                        elif key == 'ip':
                            device['ipv4'] = val
                        else:
                            device[key] = val
                    if wifis:
                        for wifi in wifis:
                            for key,val in device.iteritems():
                                if key not in wifi:
                                    wifi[key] = val
                            if 'mac' in wifi and wifi['mac'] != '':
                                rv[wifi['mac']] = Device(wifi)
                    if 'mac' in device and device['mac'] != '':
                        rv[device['mac']] = Device(device)

        return DeviceList(rv.values())

    def add_missing_fields(self,other):
        '''
        will update device in list with extra info from other device list
        '''

        for mac,device in self.devices_by_mac.iteritems():
            if mac in other:
                otherdevice = other[mac]
                for key,val in otherdevice.info.iteritems():
                    if key not in device:
                        device[key] = val
            device.add_vendor()
            
        self.cols = None
    
    def update_with( self, other, override=None):
        '''
        will add new devices if they are missing.
        if not missing and override not None, will update corresponding fields
        '''

        for device in other:
            mac = device.mac()
            if mac:
                if mac in self.devices_by_mac:
                    found = self.devices_by_mac[mac]
                    if override:
                        changed = False
                        for key in override:
                            if key not in found or found[key] != device[key]:
                                if device[key]:
                                    found[key] = device[key]
                                    changed = True
                        if changed:
                            self.changed += 1
                else:
                    self.added += 1
                    device.record_as_new()
                    self.devices_by_mac[mac] = device
        self.cols = None

    def extract_incomplete(self,live=None,keys=['name']):
        '''
        will return list of devices for which keys are missing or incomplete
        if live is a DeviceList, will only extract if also in the live list,
        to enable to investigate only devices currently available
        '''
        incomplete = []
        for device in live:
            mac = device.mac()
            if mac in self.devices_by_mac:
                existing = self.devices_by_mac[mac]

                hasMissing = False

                for key in keys:
                    if key not in existing or existing[key] == '':
                        hasMissing = True

                if hasMissing:
                    incomplete.append( device )

        return DeviceList( incomplete )

    def save_as_targets(self,fname):
        of = open(fname,'w')
        unique = {}
        for device in self.devices_list_ordered_by('ipv4'):
            ip = device['ipv4']
            if ip not in unique:
                unique[ip] = 1
                of.write( ip+'\n' )

    def save_as_json_logic(self,fname,save,force):
        if save:
            if self.has_changes() or force:
                self.save_as_json(fname)
                print 'Saved into %s' %(fname,)
            else:
                print 'No changes to save'
        else:
            if self.has_changes():
                print 'Nothing saved, use --save option to save to file'
        
                
    def save_as_json(self,fname):
        with open(fname, 'w') as outfile:
            values = self.devices_list_ordered_by('ipv4')
            json.dump(values, outfile,indent = 0, default = json_serial_defaults, sort_keys=True)
        
    def col_width(self):
        if self.cols:
            return self.cols
        
        rv = defaultdict(int)
        for x in self.devices_list():
            for field,val in x.info.iteritems():
                rv[field] = max(rv[field],len(str(val)))
        self.cols = rv
        return rv

    def display_changes(self,check=None):
        for one in self.devices_list_ordered_by('ipv4'):
            if one.has_changed(check):
                if one.is_new():
                    print '--NEW: %s' %(one,)
                else:
                    print '--CHANGED: %s' %(one,)
                one.show_changes(check)
                    
    def display_kismet(self, uuid):
        for one in self.devices_list_ordered_by('mac'):
            if 'name' in one and one['name'] != '':
                macsp = one.mac().upper().split(':')
                key = '%s_%s' %(uuid,''.join(reversed(macsp)))
                print "INSERT INTO device_names (key,name) VALUES ('%s0000','%s');" %(key, one['name'])

    def display_wireshark(self, uuid):
        for one in self.devices_list_ordered_by('ipv4'):
            print "%s %s" %(one.mac(), one['name'].replace(' ','_'))

    def display_hosts(self):
        print "# Hostnames"
        for one in self.devices_list_ordered_by('ipv4'):
            if 'hostname' in one:
                print "{ipv4:13s}\t{hostname}".format(**one.info)
        print "# Aliases"
        for one in self.devices_list_ordered_by('ipv4'):
            if 'hostname_aliases' in one:
                aliases = one['hostname_aliases']
                for alias in aliases:
                    print "{ipv4:13s}\t{hostname}".format(ipv4=one['ipv4'],hostname=alias)

    def display_static_host_mapping(self):
        jsondata = {}
        for one in self.devices_list_ordered_by('ipv4'):
            if 'hostname' in one:
                jsondata[ one['hostname'] ] = { "inet": one['ipv4'] }
        for one in self.devices_list_ordered_by('ipv4'):
            if 'hostname_aliases' in one:
                aliases = one['hostname_aliases']
                for alias in aliases:
                    jsondata[ alias ] = { "inet": one['ipv4'] }

        print json.dumps( { "system": { "static-host-mapping": { "host-name": jsondata } } }, indent=2 )

                
    def display_human(self, fields=['name','ipv4','mac','vendor']):

        orderedfields = fields;
        cols = self.col_width()
        snetwork = self.devices_list_ordered_by( fields[0] )

        print '|'.join([ '{0: <{width}}'.format(str(k) , width=cols[k]) for k in orderedfields])
        for one in snetwork:
            print '|'.join([ '{0: <{width}}'.format(str(one[k]) if k in one else '', width=cols[k]) for k in orderedfields])


    def find_one_field(self,field):
        cols = self.col_width()
        rv = None
        
        # first check exact match
        for x in cols:
            if field.lower() == x.lower():
                rv = x
                break

        # then try fuzzy patch
        if rv is None:
            for x in cols:
                if field.lower() in x.lower():
                    rv = x
                    break

        return rv
            
    def build_fields(self, displayfields, minimumfields=[]):
        
        cols = self.col_width()

        display = [self.find_one_field(x) for x in displayfields]
        extra = [self.find_one_field(x) for x in minimumfields]

        display = [x for x in display if x]
        extra = [x for x in extra if x and x not in display]

        rv = display + extra
        if not rv:
            rv = minimumfields
        return rv


class Command :
    def __init__(self,args):
        self.args = args


    def xml_file(self):
        xmlfile = self.args.xml if self.args.xml else 'list.xml'
        
        if not xmlfile.endswith('.xml'):
            xmlfile = xmlfile + '.xml'

        return xmlfile

    def default_field(self,preferred):
        field = preferred
        if len(self.args.args) > 0:
            field = args.args[0]

        return field

    
    def nmap_get_list(self):
        network = '192.168.1.0/24'
        if self.args.network:
            network = args.network
            
        subprocess.call( ['sudo', 'nmap', '-sn', '-oX', self.xml_file(), network ] )


    def nmap_get_names(self):
        subprocess.call( ['sudo', 'nmap', '-iL', self.args.targets, '-sU', '-p137,5353', '--script', 'nbstat,dns-service-discovery', '-oX', 'details.xml' ] )
        
    def cmd_show(self):
        devices = DeviceList.from_json(args.json, all=args.all)

        if self.args.display == 'kismet':
            devices.display_kismet( 'UUID' )
        elif self.args.display == 'wireshark':
            devices.display_wireshark( 'UUID')
        elif self.args.display == 'hosts':
            devices.display_hosts()
        elif self.args.display == 'static_host_mapping':
            devices.display_static_host_mapping()
        elif self.args.display == 'human':
            fields = devices.build_fields(self.args.args, ['name','mac','ipv4','vendor'])
            devices.display_human(fields=fields)
        else:
            print( 'Invalid display "{}".\nUse one of human|kismet|wireshark|hosts|static_host_mapping'.format( self.args.display ) )

        if self.args.save and self.args.force:
            devices.save_as_json( args.json)
            print 'Saved {}'.format( args.json )


    def cmd_live(self):

        if self.args.run:
            self.nmap_get_list()
            
            
        devices = DeviceList.from_json(args.json,all=True)
        fields = devices.build_fields(self.args.args, ['ipv4','mac','name','vendor','location','hostname','model'])
        live_devices = DeviceList.from_nmap_xml_file(self.xml_file())
        live_devices.add_missing_fields(devices)
        live_devices.display_human(fields)
        live_devices.save_as_targets(self.args.targets)

        print 'Found %d devices' %(len(live_devices),)

    def cmd_fields(self):
        devices = DeviceList.from_json(args.json,all=args.all)
        fields = {}
        keylen = 0
        for device in devices.devices_list():
            for key,val in device.info.iteritems():
                info = {'count':0,'sample':''}
                if key in fields:
                    info = fields[key]

                info['count'] = info['count']+1
                if len(info['sample'])<len(str(val)):
                    info['sample'] = str(val)

                keylen = max(len(key),keylen)
                
                fields[key] = info

        orderedkey = sorted(fields.keys(),key=lambda x: 100.0*fields[x]['count']/len(devices))

        for key in orderedkey:
            info = fields[key]
            keystr = '{0: <{width}}'.format(key,width=keylen)

            print '%s[%d/%d (%d%%)]: %s' %( keystr, info['count'], len(devices), 100.0*info['count'] / len(devices), info['sample'] )

    def cmd_parse(self):
        devices = DeviceList.from_json(args.json,all=True)
        re_mac = re.compile('([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}')
        re_ip  = re.compile('([0-9]{1,3}\.){3}[0-9]{1,3}')

        with open(args.args[0],'r') as infile:
            cache = dict()
            line = infile.readline()
            cnt = 0
            while( line ):
                mac = re_mac.search( line )
                ip  = re_ip.search( line )

                if mac:
                    cache[mac.group(0).upper()] = Device({'ipv4': ip.group(0) if ip else '0.0.0.0', 'mac': mac.group(0).upper(), 'lastlog':line.rstrip()})

                cnt+=1
                line = infile.readline()

            found = DeviceList(cache.values())
            found.add_missing_fields(devices)
            unknown = found.unknown_devices()
            
            fields = devices.build_fields(self.args.args[1:], ['name','mac','ipv4','vendor'])
            found.display_human(fields=fields)

            
            print 'Total %d lines, %d devices, Unknown: %d' %(cnt, len(found), len(unknown))
            if len(unknown)>0:
                unknown.display_human(fields=['mac','ipv4','vendor','lastlog'])
        
        
    def cmd_update(self):
        devices = DeviceList.from_json(args.json,all=True)
        
        if self.args.run:
            self.nmap_get_list()
            
        found = DeviceList.from_nmap_xml_file(self.xml_file())
        
        devices.update_with( found, override=['ipv4'] )
        
        print devices.status()

        devices.display_changes()
        devices.save_as_json_logic(self.args.json,self.args.save,self.args.force)

                
if __name__ == "__main__":

    commands = {
        'parse': {'attr':'cmd_parse','help':'Parse file'},
        'show': {'attr':'cmd_show','help':'show existing device from json file'},
        'update': {'attr':'cmd_update','help':'update json file from nmap or xml file' },
        'live':{'attr':'cmd_live','help':'show list host running nmap'},
        'fields':{'attr':'cmd_fields','help':'show list of available fields in the json file' }
    }
    
    description = "\n".join( [ '{}: {}'.format( k,v['help'] ) for (k,v) in commands.iteritems() ] )

    parser = argparse.ArgumentParser( description='Manage list of host form a json file\n', formatter_class=argparse.RawTextHelpFormatter )
    parser.add_argument( 'command', metavar='Command', help='command is one of\n' + description )
    parser.add_argument( 'args', metavar='Arguments', nargs='*' )
    parser.add_argument( '-a', '--all', action='store_true', help='Show all devices, including disabled')
    parser.add_argument( '-c', '--csv', metavar='CSVFILE', help='csv file to merge')
    parser.add_argument( '-d', '--display', help='display style human|kismet|wireshark|hosts|static_host_mapping', default='human' )
    parser.add_argument( '-f', '--force', action='store_true', help='Force save during update, helpful for reformatting file' )
    parser.add_argument( '-j', '--json', metavar='JSONFILE', help='json file', default='network.json')
    parser.add_argument( '-l', '--lan', metavar='LANFILE', help='lan file to merge')
    parser.add_argument( '-n', '--network', help='network definition for nmap', default='192.168.1.0/24')
    parser.add_argument( '-r', '--run', action='store_true', help='run nmap, else just use last cached file' )
    parser.add_argument( '-s', '--save', action='store_true', help='Save any update or change to the list' )
    parser.add_argument( '-t', '--targets', help='Target files either for read in list command or save in live command', default='targets.out' )
    parser.add_argument( '-x', '--xml', metavar='XMLFILE', help='xml file with nmap output' )

    args = parser.parse_args()

    command = Command(args)

    if args.command not in commands:
        print '\nUnknown Command {}\n'.format( args.command )
        parser.print_help()
    else:
        getattr(command,commands[args.command]['attr'])()

