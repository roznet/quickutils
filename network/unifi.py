#!/usr/bin/python
#

import argparse
from requests import Session
import re
import json
import urllib3
from pprint import pprint
import os
from lancheck import DeviceList,Device

class UnifiController(object):

    #### SETUP #
    def __init__(self, verify_ssl=False):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        if os.path.isfile( '.unifi.json' ):
            with open( '.unifi.json', 'r') as jsonfile:
                self.config = json.load( jsonfile )
        else:
            self.report_invalid_config()

        self.validate_config()
        self.session = Session()
        # in case we ever get a certificate
        self.verify_ssl = False
        self.last_response = None
        
    def report_invalid_config(self):
        print( "Invalid .unifi.json, saving template")
        fname = '.unifi.json'
        with open( fname,'w') as outfile:
            json.dump( { 'login':{'username':'ubnt','password':'ubnt'},'site':'default','baseurl':'https://localhost:8443'}, outfile, indent=0,sort_keys=True)
        exit()
    
    def validate_config(self):
        valid = "login" in self.config and "site" in self.config and "baseurl" in self.config
        if valid:
            valid = "username" in self.config["login"] and "password" in self.config["login"]

        if not valid:
            self.report_invalid_config()

    #### UTILS #
    def construct_url(self,urldef):
        fullurldef = '{baseurl}'+urldef
        rv = fullurldef.format(site=self.config['site'],baseurl=self.config['baseurl'])

        return rv

    def post(self,urldef,pydata=None):
        jsondata = json.dumps(pydata) if pydata else None
        self.last_response = self.session.post(self.construct_url(urldef),data=jsondata,verify=self.verify_ssl)


    def put(self,urldef,pydata=None):
        jsondata = json.dumps(pydata) if pydata else None
        self.last_response = self.session.put(self.construct_url(urldef),data=jsondata,verify=self.verify_ssl)

    def get(self,urldef,pydata=None):
        jsondata = json.dumps(pydata) if pydata else None
        self.last_response = self.session.get(self.construct_url(urldef),data=jsondata)


    def json(self):
        return self.last_response.json()['data']

    def save_json(self,fname='unifi.json'):
        with open(fname, 'w') as outfile:
            values = self.json()
            json.dump(values, outfile,indent = 2, sort_keys=True)
    
    def status_code(self):
        return self.last_response.status_code

    #### REQUESTS #
    
    def login(self):
        self.post( "/api/login",pydata=self.config['login'] )
        if self.status_code() == 400:
            pprint( self.json() )
            raise Exception("Failed to log in to api with provided credentials")

        
    def logout(self):
        self.get("/logout")
        self.session.close()


    def add_client(self,list):
        '''
        list of dict with "mac" and "name"
        '''

        data = [{'data':{'mac':x['mac'], 'name':x['name']}} for x in list]
        jsondata = { "objects":data }

        self.post( "/api/s/{site}/group/user", pydata=jsondata )
        pprint(self.json())

    def list_known_devices(self):
        self.get("/api/s/{site}/stat/device/")
        return self.json()

    def list_known_clients(self):
        self.get("/api/s/{site}/list/user/")
        return self.json()

    def list_connected_clients(self):
        self.get("/api/s/{site}/stat/sta")
        return self.json()

    def upload_network(self):
        with open('network.json','r') as fp:
            list = json.load(fp)
        self.add_client(list)

    def change_user_info(self,orig,new):
        if '_id' in orig:
            id = orig['_id']

            updated = {}
            for key,val in new.iteritems():
                updated[key] = val
            url = "/api/s/{site}/rest/user/" + id

            self.put( url, pydata=updated )
        else:
            raise Exception('_id not found, cannot change info')

    def device_list_from_unifi(self):
        all = self.json()
        rv = DeviceList([])
        for one in all:
            dev = self.device_from_unifi(one)
            if dev:
                rv.update_with( DeviceList( [ dev ] ) )
            wifi = self.wifi_devices_from_unifi(one)
            if wifi:
                rv.update_with( wifi )
        return rv
        
    def device_from_unifi(self,one):
        defs = { 'ip':'ipv4', 'name':'name', 'hostname':'hostname', 'mac':'mac', 'model':'model','_id':'_id' }
        
        info = {}
        for (key,mapped) in defs.iteritems():
            if key in one:
                info[mapped] = one[key]

        if 'mac' in info:
            info['mac'] = info['mac'].upper()
                
        if 'name' not in info and 'hostname' in info:
            info['name'] = info['hostname']

        # special case for router/gateway
        if 'ipv4' in info and info['ipv4'] == '81.187.137.231':
            info['ipv4'] = '192.168.1.1'
            
        models = { 'U7NHD':    { 'model': 'UniFi AP-nanoHD', 'image':'unifi_U7NHD.png' },
                   'U7HD':     { 'model': 'UniFi Ap-HD',     'image':'unifi_U7NHD.png' },
                   'UGW4' :    { 'model': 'UniFi Security Gateway 4P', 'image':'unifi_UGW4.png'},
                   'US24P250': { 'model': 'UniFi Switch 24 POE', 'image': 'unifi_US24P250.png'},
                   'US8P60':   { 'model': 'UniFi Switch 8 POE',  'image': 'unifi_US8P60.png' }
                   }
        
        if 'model' in info and info['model'] in models:
            model = info['model']
            for (k,v) in models[ model ].iteritems():
                info[k] = v

        if 'ipv4' not in info:
            return None
        
        dev =  Device(info)
        dev.add_vendor()
        return dev

    def wifi_devices_from_unifi(self,one):
        main = self.device_from_unifi(one)
        if not main:
            return None
        rv = []
        if 'vap_table' in one:
            remap = {'channel':'channel','essid':'ssid','bssid':'mac'}
            for wifi in one['vap_table']:
                newinfo = dict(main.info)
                for (key,mapped) in remap.iteritems():
                    newinfo[mapped] = wifi[key]
                if newinfo['channel'] > 20:
                    newinfo['frequency'] = "5 Ghz"
                else:
                    newinfo['frequency'] = "2.4 Ghz"
                newinfo['network'] = 'Chiddingstone'

                if 'mac' in newinfo:
                    newinfo['mac'] = newinfo['mac'].upper()
                
                rv += [ Device( newinfo ) ]
        if rv:
            return DeviceList( rv )
        else:
            return None
    
    def change_name_for_mac(self,mac,newname):
        self.list_known_clients()

        found = None

        for one in unifi.json():
            if one['mac'].lower() == mac.lower():
                found = one
                break

        if found:
            pprint( found)
            unifi.change_user_info(found,{'name':newname})

class Command :
    def __init__(self,args):
        self.args = args
        
    def cmd_list_devices(self):
        unifi = UnifiController()
        unifi.login()
        unifi.list_known_devices()
        devicelist = unifi.device_list_from_unifi()
        fields = devicelist.build_fields(self.args.args, ['name','ipv4','vendor','ssid','mac','frequency','channel'])
        devicelist.display_human(fields)
        if self.args.save:
            devicelist.save_as_json('unifi.json')

    def cmd_list_clients(self):
        unifi = UnifiController()
        unifi.login()
        unifi.list_connected_clients()
        clientlist = unifi.device_list_from_unifi()
        if self.args.save:
            unifi.save_json('unifi.json')
        fields = clientlist.build_fields(self.args.args, ['name','mac','ipv4','vendor'])
        clientlist.display_human(fields)


    def cmd_pull_to_network(self):
        unifi = UnifiController()
        unifi.login()
        unifi.list_known_devices()
        devicelist = unifi.device_list_from_unifi()
        unifi.list_connected_clients()
        clientlist = unifi.device_list_from_unifi()
        list = devicelist
        list.update_with( clientlist )
        fields = list.build_fields(self.args.args, ['name','mac','ipv4','vendor'])
        list.display_human(fields)

        devices = DeviceList.from_json(args.network,all=True)
        devices.update_with( list, override=['ipv4','channel','frequency'])

        devices.display_changes()
        
        print devices.status()
        
        devices.save_as_json_logic(self.args.network,self.args.save,self.args.force)

    def cmd_push_to_unifi(self):
        unifi = UnifiController()
        unifi.login()
        unifi.list_known_devices()
        devicelist = unifi.device_list_from_unifi()
        unifi.list_connected_clients()
        clientlist = unifi.device_list_from_unifi()
        list = devicelist
        list.update_with( clientlist )
        list.clear_changes()
        
        devices = DeviceList.from_json(args.network,all=True)
        list.update_with( devices )

        list.display_changes(['name'])
        
        print list.status()

        
    def cmd_map_names(self):
        devices = DeviceList.from_json('network.json')

        unifi = UnifiController()
        unifi.login()
        known = unifi.list_known_clients()

        for x in known:

            mac = str(x['mac']).upper()
            if mac in devices:
                device = devices[ mac ]
                print( x )
                #print( 'change name to {}'.format( device['name'] ) )
                #unifi.change_user_info(x,{'name':device['name']})
            else:
                print mac

            
        
if __name__ == "__main__":

    commands = {
        'devices': {'attr':'cmd_list_devices','help':'List devices from the unifi controller' },
        'clients': {'attr':'cmd_list_clients','help':'List clients from the unifi controller' },
        'pull': {'attr':'cmd_pull_to_network','help':'Get clients list from controller and merge new clients into local json file' },
        'push': {'attr':'cmd_push_to_unifi','help':'Update names on the controller from the local database'}
    }

    description = "\n".join( [ '  {}: {}'.format( k,v['help'] ) for (k,v) in commands.iteritems() ] )

    parser = argparse.ArgumentParser( description="Interact with the unifi controller using:\n", formatter_class=argparse.RawTextHelpFormatter )
    parser.add_argument( 'command', metavar='Command', help='command is devices, clients\n' + description )
    parser.add_argument( 'args', metavar='Arguments', nargs='*' )
    parser.add_argument( '-e', '--execute', action='store_true', help='Force execution of push on server' )
    parser.add_argument( '-f', '--force', action='store_true', help='Force save of network file during pull, helpful for reformatting file' )
    parser.add_argument( '-n', '--network', metavar='JSONFILE', help='network json file', default='network.json')
    parser.add_argument( '-s', '--save', action='store_true', help='Save any update or change to the list' )

    args = parser.parse_args()

    command = Command(args)

    if args.command in commands:
        getattr(command,commands[args.command]['attr'])()
    else:
        print 'Invalid command "{}"'.format( args.command)
        parser.print_help()


