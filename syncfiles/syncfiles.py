#!/usr/bin/python
#
#

import sys
import subprocess
import os
import hashlib
import argparse
import json
from pprint import pprint
import shutil

def file_hash(filepath):
    openedFile = open(filepath)
    readFile = openedFile.read()

    hash = hashlib.sha1(readFile)

    return hash.hexdigest()


class FilePair :
    def __init__(self,dir,src_dir,file_rel_path):
        self.dir = dir
        self.src = src_dir
        self.file_rel_path = file_rel_path

    def __repr__(self):
        return '%s (%s %s)' %( self.file_rel_path, self.dir, self.src )

    def src_file(self):
        return os.path.join( self.src, self.file_rel_path )

    def src_file_exists(self):
        return os.path.isfile( self.src_file() )
    
    def dir_file(self):
        return os.path.join( self.dir, self.file_rel_path )

    def dir_file_exists(self):
        return os.path.isfile( self.dir_file() )

    def is_match(self):
	if not self.src_is_readable() or not self.dir_is_readable():
		return False
        return file_hash( self.dir_file() ) == file_hash( self.src_file() )

    def src_is_readable(self):
        return os.access( self.src_file(), os.R_OK )
    def dir_is_readable(self):
        return os.access( self.dir_file(), os.R_OK )

    
class Config :
    def __init__(self,args=None):
        self.args = args
        self.verbose = self.args.verbose
        self.cwd = os.getcwd()
        self.find_config_file()
        self.expand_dirs()
        self.max_len_dir = 0
        self.max_len_src = 0

    def find_config_file(self):
        cwdcomponents = self.cwd.split('/')
        found = False
        while(found is False and cwdcomponents):
            candidate = os.path.join( '/'.join(cwdcomponents), '.syncfiles' )
            if os.path.isfile(candidate):
                self.basedir = '/'.join(cwdcomponents)
                config_file = open( candidate, 'r' )
                self.defs = json.load( config_file )

                found = True
            else:
                cwdcomponents = cwdcomponents[:-1]

        if found is False:
            print( "Couldn't locate a .syncfile" )
            sys.exit( 1 )

    def expand_dirs(self):
        self.expand_dir_to_src = {}
        self.expand_src_to_dir = {}
        self.expand_src_to_src = {}
        
        for (dir,src) in self.defs["dirmap"].iteritems():
            if( dir == '.'):
                expand_dir = self.basedir
            else:
                expand_dir = os.path.join( self.basedir, dir )
                
            expand_src = os.path.expanduser( src )

            if expand_dir.startswith( self.cwd ):
                self.expand_dir_to_src[expand_dir] = expand_src
                self.expand_src_to_dir[expand_src] = dir
                self.expand_src_to_src[expand_src] = src

        self.ignore_map = {os.path.join( self.basedir, '.syncfiles' ):1}
        if 'ignore' in self.defs:
            for dir in self.defs['ignore']:
                to_ignore = os.path.join( self.basedir, dir )
                if os.path.exists( to_ignore ):
                    self.ignore_map[to_ignore] = 1

    def construct_file_pairs(self,files):
        rv = []
        self.verbose = False
        for x in files:
            if os.path.isfile( x ):
                candidate = os.path.join( self.cwd, x )
                if self.verbose:
                    print "START %s [%s]" %(x,candidate)
                for dir_path, src in self.expand_dir_to_src.iteritems():
                    if candidate.startswith( dir_path):
                        if self.verbose:
                            print "  CHECK %s %s" %(dir_path, src )
                        rel_path = candidate.replace( dir_path + '/', '', 1 )
                        try_dir_path = os.path.join( dir_path, rel_path )
                        try_src_path = os.path.join( src, rel_path )
                        if os.path.isfile( try_dir_path ) and os.path.isfile( try_src_path):
                            if self.verbose:
                                print '%s %s %s'%(dir_path, src, rel_path)

                            pair = FilePair( dir_path, src, rel_path )
                            self.max_len_dir = max(self.max_len_dir, len(self.display_dir_file(pair)))
                            self.max_len_src = max(self.max_len_src, len(self.display_src_file(pair)))
                            rv += [ pair ]

                        else:
                            if self.verbose:
                                print '  NOT FOUND in %s' %(dir_path, )
                        
                    
            else:
                for src_path, dir in self.expand_src_to_dir.iteritems():
                    candidate = os.path.join( src_path, x )
                    if os.path.isfile( candidate ):
                        rel_path = x
                        pair = FilePair( dir, src_path, rel_path )
                        self.max_len_dir = max(self.max_len_dir, len(self.display_dir_file(pair)))
                        self.max_len_src = max(self.max_len_src, len(self.display_src_file(pair)))
                        rv += [ pair ]

        return rv
                
                
    def discover_file_pairs(self,rel_dir = None, rel_src = None):
        file_pairs = []
        for (dir,src) in self.expand_dir_to_src.iteritems():
            search_dir = dir if rel_dir is None else os.path.join(dir, rel_dir )
            if rel_src and rel_src != src:
                continue
            if not os.path.isdir( search_dir ):
                continue
            dir_files = os.listdir( search_dir )
            for dir_file in dir_files:
                rel_file = dir_file if rel_dir is None else os.path.join(rel_dir, dir_file)
                full_dir_file = os.path.join(search_dir,dir_file)


                if full_dir_file in self.ignore_map:
                    if self.verbose:
                        print "Ignoring file %s" %(full_dir_file,)
                    continue
                
                if os.path.isdir( full_dir_file ):
                    if not full_dir_file in self.expand_dir_to_src:
                        next_rel_dir = dir_file if rel_dir is None else os.path.join( rel_dir, dir_file )
                        file_pairs += self.discover_file_pairs(next_rel_dir,src)

                else:
                    if full_dir_file in self.expand_dir_to_src:
                        if self.verbose:
                            print "skip source %s" %(full_dir_file,)
                    else:
                        pair = FilePair( dir, src, rel_file )
                        self.max_len_dir = max(self.max_len_dir, len(self.display_dir_file(pair)))
                        self.max_len_src = max(self.max_len_src, len(self.display_src_file(pair)))
                        file_pairs += [ pair ]
        return file_pairs

    def list_file_pairs(self):
        if self.args and self.args.files:
            return self.construct_file_pairs(self.args.files)
        else:
            return self.discover_file_pairs()
    
    def display_dir_file(self, filepair ):
        dir_path = self.expand_src_to_dir[filepair.src]
        if dir_path == '.':
            return filepair.file_rel_path.ljust(self.max_len_dir,' ' )
        else:
            return os.path.join( dir_path, filepair.file_rel_path).ljust(self.max_len_dir, ' ')


    def display_src_file(self, filepair):
        src_path = self.expand_src_to_src[filepair.src]
        if src_path == '.':
            return filepair.file_rel_path.ljust(self.max_len_src,' ')
        else:
            return os.path.join( src_path, filepair.file_rel_path).ljust(self.max_len_src,' ')
        
        
    def format_status(self, filepair ):
        
        if filepair.src_file_exists():
            if not filepair.src_is_readable():
                return '! %s   %s' %(self.display_dir_file(filepair), self.display_src_file(filepair) )
            elif not filepair.dir_is_readable():
                return 'A %s   %s' %(self.display_dir_file(filepair), self.display_src_file(filepair) )
            else:
                if filepair.is_match():
                    return '. %s = %s' %(self.display_dir_file(filepair), self.display_src_file(filepair) )
                else:
                    return 'M %s %s %s' %(self.display_dir_file(filepair), self.display_compare_dir_src(filepair), self.display_src_file(filepair) )
        else:
            return '? %s  [%s]' %(self.display_dir_file(filepair), self.display_src_file(filepair) )

    def display_compare_dir_src(self, filepair ):
        m_src = os.path.getmtime( filepair.src_file() )
        m_dir = os.path.getmtime( filepair.dir_file() )

        if( m_src == m_dir ):
            return '='
        return '<' if m_dir < m_src else '>'


    def cmd_pull(self):
        exists = [x for x in self.list_file_pairs() if x.src_file_exists() ]

        for x in exists:
            if not x.is_match():
                if not x.src_is_readable():
                    print 'cannot read: %s' % (x.src_file(),)
                elif not x.dir_is_readable():
                    print 'cannot read: %s' %(x.dir_file(),)
                else:
                    print 'cp %s %s' % (x.src_file(), x.dir_file() )
                    if self.args.execute is True:
                        shutil.copyfile( x.src_file(), x.dir_file() )


    def cmd_push(self):
        exists = [x for x in self.list_file_pairs() if x.src_file_exists() ]

        for x in exists:
            if not x.is_match():
                print 'cp %s %s' % (x.dir_file(), x.src_file() )
                if self.args.execute is True:
                    shutil.copyfile( x.dir_file(), x.src_file() )


                    

    def cmd_status(self):
        exists = [ x for x in self.list_file_pairs() if x.src_file_exists() ]
        unknown = [ x for x in self.list_file_pairs() if not x.src_file_exists() ]

        for x in exists:
            print self.format_status(x)
        print
        for x in unknown:
            print self.format_status(x)


    def cmd_apply(self,fns):
        exists = [ x for x in self.list_file_pairs() if x.src_file_exists() ]
        for x in exists:
            fns(x)
            
    def cmd_difftool(self):
        exists = [ x for x in self.list_file_pairs() if x.src_file_exists() ]
        for x in exists:
            if not x.is_match():
                print self.format_status(x)
                subprocess.call( [ 'ksdiff', '--partial-changeset', x.dir_file(), x.src_file() ]  )
                
        
    def cmd_diff(self):
        exists = [ x for x in self.list_file_pairs() if x.src_file_exists() ]

        for x in exists:
	    if not x.src_is_readable():
		continue	
            if not x.is_match():
                print self.format_status(x)
                subprocess.call( [ 'diff', x.dir_file(), x.src_file() ]  )

if __name__ == "__main__":
                
    commands = {
        'status':{'attr':'cmd_status','help':'Show status of files'},
        'diff':{'attr':'cmd_diff','help':'Show diff for modified files'},
        'difftool':{'attr':'cmd_difftool','help':'Show diff for modified files in ksdiff'},
        'pull':{'attr':'cmd_pull','help':'copy to local the original files'},
        'push':{'attr':'cmd_push','help':'push local file to the original location'}
    }
    
    description = "\n".join( [ '  {}: {}'.format( k,v['help'] ) for (k,v) in commands.iteritems() ] )
    
    parser = argparse.ArgumentParser( description='Check configuration', formatter_class=argparse.RawTextHelpFormatter )
    parser.add_argument( 'command', metavar='Command', help='command to execute:\n' + description)
    parser.add_argument( '-e', '--execute', action='store_true', help='actually execute the commands otherwise just print' )
    parser.add_argument( '-v', '--verbose', action='store_true', help='verbose output' )
    parser.add_argument( 'files',    metavar='FILES', nargs='*' )
    args = parser.parse_args()

    command = Config(args)

    if args.command in commands:
        getattr(command,commands[args.command]['attr'])()
    else:
        print 'Invalid command "{}"'.format( args.command)
        parser.print_help()


