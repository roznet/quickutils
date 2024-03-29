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
import distutils.spawn
import hashlib
import argparse
import json
from pprint import pprint
import shutil
import fnmatch
import platform

def file_hash(filepath):
    openedFile = open(filepath, 'rb')
    readFile = openedFile.read()

    hash = hashlib.sha1(readFile)
    return hash.hexdigest()


class FilePair :
    def __init__(self,dir,src_dir,file_rel_path):
        '''
        FilePair Object:

        dir: directory where the file is stored in git/sync repo (ex: /path/to/syncfiles/etc/apache2)
        src_dir: directory where the file is supposed to be (ex: /etc/apache2)
        file_rel_path: rel path inside dir and src_dir (ex: sites/default.conf )
        '''
        self.dir = dir
        self.src = src_dir
        self.file_rel_path = file_rel_path

    def __repr__(self):
        return 'FilePair: %s (%s %s)' %( self.file_rel_path, self.dir, self.src )

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
        '''
        Find the config file in the current directory or any parent directory
        '''
        cwdcomponents = self.cwd.split('/')
        found = False
        while(found is False and cwdcomponents):
            candidate = os.path.join( '/'.join(cwdcomponents), '.syncfiles' )
            if os.path.isfile(candidate):
                self.basedir = '/'.join(cwdcomponents)
                config_file = open( candidate, 'r' )
                self.defs = json.load( config_file )
                self.validate_config()
                
                found = True
            else:
                cwdcomponents = cwdcomponents[:-1]
                
        if found is False:
            print( "Couldn't locate a .syncfile" )
            sys.exit( 1 )

        self.git_root = None
        
        if not self.args.nogit:
            git_root_out = subprocess.run( ['git', 'rev-parse', '--show-toplevel'], capture_output=True)
            if git_root_out.returncode == 0:
                self.git_root = git_root_out.stdout.decode( 'utf-8' ).splitlines()[0]
                if self.verbose:
                    print( 'CONFIG: Using Git Files only, git_root={}'.format( self.git_root ) )
            else:
                if self.verbose:
                    print( 'CONFIG: Not in a git repository' )

        if self.verbose:
            print( 'CONFIG: {} base={} git={}'.format(candidate,self.basedir, self.git_root ) )

    def validate_config(self):
        valid = True
        if 'validate' in self.defs:
            valid = eval( self.defs['validate'] )
            if not valid:
                if not self.args.force:
                    print( 'Skipping, Validation "{}" evaluates to False'.format( self.defs['validate'] ) )
                    sys.stdout.flush()
                    os._exit(0)
                else:
                    if self.verbose:
                        print( 'WARNING: validation "{}" evaluates to False'.format( self.defs['validate'] ) )
            else:
                if self.verbose:
                    print( 'CONTINUE: Validation "{}" evaluates to True'.format( self.defs['validate'] ) );
                    
        return valid
    
    def expand_dirs(self):
        '''
          Find all the source dir and destination dir relevant from the config file
          basedir = '/path/to/base'
          Ex: dirmap: 'etc/apache2': '/etc/apache2'
               dir_to_src = { '/path/to/base/etc/apache2': '/etc/apache2', '/path/to/base/bin }
               src_to_dir = { '/etc/apache2': 'etc/apache2', '/Users/xxx/bin':'bin' }
               src_to_src = { '/etc/apache2': '/etc/apache2', '/Users/xxx/bin': '~/bin' } 

        '''
        self.expand_dir_to_src = {}
        self.expand_src_to_dir = {}
        self.expand_src_to_src = {}

        for (dir,src) in self.defs["dirmap"].items():
            if( dir == '.'):
                expand_dir = self.basedir
            else:
                expand_dir = os.path.join( self.basedir, dir )
                
            expand_src = os.path.expanduser( src )
            
            self.expand_dir_to_src[expand_dir] = expand_src
            self.expand_src_to_dir[expand_src] = dir
            self.expand_src_to_src[expand_src] = src
        
        # Always ignore syncfiles
        self.ignore_map = {os.path.join( self.basedir, '.syncfiles' ):1}
        self.ignore_pattern = {}
        if 'ignore' in self.defs:
            for dir in self.defs['ignore']:
                to_ignore = os.path.join( self.basedir, dir )
                if os.path.exists( to_ignore ):
                    self.ignore_map[to_ignore] = 1
                else:
                    self.ignore_pattern[ dir ] = 1

    def should_ignore(self,filepath,rel_file):
        for ignore_path in self.ignore_map:
            if filepath.startswith( ignore_path ):
                return True

        for pattern in self.ignore_pattern:
            if fnmatch.fnmatch( filepath, pattern ):
                return True

        return False
            
    def construct_file_pairs(self,files):
        '''
           Input is array, each entry:
              if a file: limit to that file if exists
              if a dir: search that dir
        '''
        rv = []
        self.verbose = False
        for x in files:
            if os.path.isfile( x ):
                candidate = os.path.join( self.cwd, x )
                if self.verbose:
                    print( "START %s [%s]" %(x,candidate) )
                for dir_path, src in self.expand_dir_to_src.items():
                    if candidate.startswith( dir_path):
                        if self.verbose:
                            print( "  CHECK %s %s" %(dir_path, src ) )
                        rel_path = candidate.replace( dir_path + '/', '', 1 )
                        try_dir_path = os.path.join( dir_path, rel_path )
                        try_src_path = os.path.join( src, rel_path )
                        if os.path.isfile( try_dir_path ) and os.path.isfile( try_src_path):
                            if self.verbose:
                                print( '%s %s %s'%(dir_path, src, rel_path) )

                            pair = FilePair( dir_path, src, rel_path )
                            self.max_len_dir = max(self.max_len_dir, len(self.display_dir_file(pair)))
                            self.max_len_src = max(self.max_len_src, len(self.display_src_file(pair)))
                            rv += [ pair ]

                        else:
                            if self.verbose:
                                print( '  NOT FOUND in %s' %(dir_path, ))

            elif os.path.isdir( x ):

                candidate = self.cwd if x == '.' else os.path.join( self.cwd, x)
                found = None
                found_src = None
                for dir_path, src in self.expand_dir_to_src.items():
                    if candidate.startswith( dir_path ) and (found == None or len(dir_path) > len(found)):
                        found = dir_path
                        found_src = src
                if found:
                    rel_dir = candidate[len(found)+1:]

                    rv += self.discover_file_pairs( rel_dir, found_src )
            else:
                for src_path, dir in self.expand_src_to_dir.items():
                    candidate = os.path.join( src_path, x )
                    if self.verbose:
                        print( 'Candidate {}'.format(candidate) )
                    if os.path.isfile( candidate ):
                        rel_path = x
                        pair = FilePair( dir, src_path, rel_path )
                        self.max_len_dir = max(self.max_len_dir, len(self.display_dir_file(pair)))
                        self.max_len_src = max(self.max_len_src, len(self.display_src_file(pair)))
                        rv += [ pair ]

        return rv

    def discover_file_pairs(self,rel_dir = None, rel_src = None):
        '''
        rel_dir is None to look at all the source or the relative director off the src base
        rel_src is None or the src the rel_dir is off

        returns all the file pairs below that directory

        Ex: source = /etc/apache2 -> rel_dir = apache2, rel_src = /etc
        '''

        file_pairs = []
        for (dir,src) in self.expand_dir_to_src.items():
            if self.verbose:
                print( 'LOOK: dir={} src={}'.format( dir, src ) )
            search_dir = dir if rel_dir is None else os.path.join(dir, rel_dir )
            if rel_src and rel_src != src:
                continue
            if not os.path.isdir( search_dir ):
                continue
            dir_files = os.listdir( search_dir )

            for dir_file in dir_files:
                rel_file = dir_file if rel_dir is None else os.path.join(rel_dir, dir_file)
                full_dir_file = os.path.join(search_dir,dir_file)

                if self.should_ignore( full_dir_file, rel_file ):
                    if self.verbose:
                        print( "IGNORE: file %s" %(full_dir_file,) )
                    continue

                if os.path.isdir( full_dir_file ):
                    if not full_dir_file in self.expand_dir_to_src:
                        next_rel_dir = dir_file if rel_dir is None else os.path.join( rel_dir, dir_file )
                        file_pairs += self.discover_file_pairs(next_rel_dir,src)

                else:
                    if full_dir_file in self.expand_dir_to_src:
                        if self.verbose:
                            print( "SKIP: source %s" %(full_dir_file,) )
                    else:
                        pair = FilePair( dir, src, rel_file )
                        if self.verbose:
                            print( 'FOUND: discovered %s'%(pair,) )
                        self.max_len_dir = max(self.max_len_dir, len(self.display_dir_file(pair)))
                        self.max_len_src = max(self.max_len_src, len(self.display_src_file(pair)))
                        file_pairs += [ pair ]
        return file_pairs


    def construct_git_pairs(self):
        file_pairs = []
        cmd = [ 'git', 'ls-files' ]
        if self.args.files:
            cmd += self.args.files
        git_ls_out = subprocess.run( cmd, capture_output=True)
        if git_ls_out.returncode == 0:
            files = git_ls_out.stdout.decode( 'utf-8' ).splitlines()

        for one in files:
            full_path_file = os.path.join( os.getcwd(), one )
            found_dir = None
            for (repo_dir,src_dir) in self.expand_dir_to_src.items():
                if full_path_file.startswith( repo_dir ) and ( found_dir is None or len(found_dir) < len( repo_dir) ):
                    found_dir = repo_dir
            if found_dir:
                # in repo (+1 for the /)
                file_rel_path = full_path_file[ len(found_dir)+1: ]

                if self.should_ignore( full_path_file, file_rel_path ):
                    if self.verbose:
                        print( "IGNORE: file {}".format( full_path_file ) )
                    continue

                pair = FilePair(found_dir, self.expand_dir_to_src[found_dir], file_rel_path);
                self.max_len_dir = max(self.max_len_dir, len(self.display_dir_file(pair)))
                self.max_len_src = max(self.max_len_src, len(self.display_src_file(pair)))
                file_pairs += [ pair ]
                if self.verbose:
                    print( 'FOUND: from git {}'.format(pair) )
                
        return file_pairs
    
    def list_file_pairs(self):
        if self.git_root:
            return self.construct_git_pairs()
        elif self.args and self.args.files:
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
        

    def output_status(self, file_pair ):
        status = self.format_status( file_pair );
        if status:
            print( status );

        return status
            
    def format_status(self, filepair ):
        if filepair.src_file_exists():
            if not filepair.src_is_readable():
                return '! %s   %s' %(self.display_dir_file(filepair), self.display_src_file(filepair) )
            elif not filepair.dir_is_readable():
                return 'A %s   %s' %(self.display_dir_file(filepair), self.display_src_file(filepair) )
            else:
                if filepair.is_match():
                    if self.args.all:
                        return '. %s = %s' %(self.display_dir_file(filepair), self.display_src_file(filepair) )
                    else:
                        return '';
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


    def cmd_sync(self):
        exists = [x for x in self.list_file_pairs() if x.src_file_exists() ]

        for x in exists:
            if not x.is_match():
                if not x.src_is_readable():
                    print( 'cannot read: %s' % (x.src_file(),) )
                elif not x.dir_is_readable():
                    print( 'cannot read: %s' %(x.dir_file(),))
                else:
                    m_src = os.path.getmtime( x.src_file() )
                    m_dir = os.path.getmtime( x.dir_file() )
                    if m_dir < m_src:
                        self.copyfile(x.src_file(), x.dir_file() )
                    else:
                        self.copyfile(x.dir_file(), x.src_file() )

        self.execute_warning()

    def cmd_pull(self):
        exists = [x for x in self.list_file_pairs() if x.src_file_exists() ]

        for x in exists:
            if not x.is_match():
                if not x.src_is_readable():
                    print( 'cannot read: %s' % (x.src_file(),) )
                elif not x.dir_is_readable():
                    print( 'cannot read: %s' %(x.dir_file(),))
                else:
                    self.copyfile(x.src_file(), x.dir_file() )
                    
        self.execute_warning()


    def cmd_push(self):
        exists = [x for x in self.list_file_pairs() if x.src_file_exists() ]

        for x in exists:
            if not x.is_match():
                self.copyfile(x.dir_file(), x.src_file() )

        self.execute_warning()
                    

    def copyfile(self,from_path,to_path):
        if self.args.execute:
            print( 'cp {} {}'.format(from_path, to_path ) )
            try:
                shutil.copyfile( from_path, to_path )
            except IOError as io_err:
                to_dir = os.path.dirname( to_path )
                os.makedirs( to_dir )
                shutil.copyfile( from_path, to_path )
        else:    
            print( 'cp {} {}'.format(from_path, to_path ) )
            
    def execute_warning(self):
        if not self.args.execute:
            print()
            print( 'Preview mode. Files were not copied, add --execute option to copy' )
        
    def cmd_install(self):
        for x in self.list_file_pairs():
            if not x.src_file_exists() or not x.is_match():
                self.copyfile(x.dir_file(), x.src_file() )

        self.execute_warning()
                    
    def cmd_status(self):
        exists = [ x for x in self.list_file_pairs() if x.src_file_exists() ]
        unknown = [ x for x in self.list_file_pairs() if not x.src_file_exists() ]

        done = False
        for x in exists:
            if self.output_status(x):
                done = True;

        for x in unknown:
            if self.output_status(x):
                done = True;
        if not done and not self.args.all:
            print( 'All up to date' )


    def cmd_apply(self,fns):
        exists = [ x for x in self.list_file_pairs() if x.src_file_exists() ]
        for x in exists:
            fns(x)

    def cmd_difftool(self):
        tool = None
        if self.args.difftool == 'auto':
            if distutils.spawn.find_executable( 'ksdiff' ):
                tool = ['ksdiff', '--partial-changeset']
            elif distutils.spawn.find_executable( 'icdiff' ):
                tool = ['icdiff']
            else:
                tool = ['vimdiff']
        elif self.args.difftool.startswith( 'k' ):
                tool = ['ksdiff', '--partial-changeset']
        elif self.args.difftool.startswith( 'i'):
                tool = ['icdiff']
        elif self.args.difftool.startswith( 'v' ):
                tool = ['vimdiff']
            
        exists = [ x for x in self.list_file_pairs() if x.src_file_exists() ]
        for x in exists:
            if not x.is_match():
                self.output_status(x)
                process_arg = tool + [ x.dir_file(), x.src_file() ]

                subprocess.call( process_arg )

    def cmd_diff(self):
        exists = [ x for x in self.list_file_pairs() if x.src_file_exists() ]

        for x in exists:
            if not x.src_is_readable():
                continue
            if not x.is_match():
                self.output_status(x)
                subprocess.call( [ 'diff', x.dir_file(), x.src_file() ]  )

if __name__ == "__main__":
                
    commands = {
        'status':{'attr':'cmd_status','help':'Show status of files'},
        'install':{'attr':'cmd_install','help':'Copy all the missing files to source'},
        'diff':{'attr':'cmd_diff','help':'Show diff for modified files'},
        'difftool':{'attr':'cmd_difftool','help':'Show diff for modified files in ksdiff or vimdiff'},
        'sync':{'attr':'cmd_sync','help':'copy most recent files to older one'},
        'pull':{'attr':'cmd_pull','help':'copy to local the original files'},
        'push':{'attr':'cmd_push','help':'push local file to the original location'}
    }
    
    description = "\n".join( [ '  {}: {}'.format( k,v['help'] ) for (k,v) in commands.items() ] )
    
    parser = argparse.ArgumentParser( description='Check configuration', formatter_class=argparse.RawTextHelpFormatter )
    parser.add_argument( 'command', metavar='Command', help='command to execute:\n' + description)
    parser.add_argument( '-a', '--all', action='store_true', help='Show all files including those unchanged', default=False )
    parser.add_argument( '-f', '--force', action='store_true', help='run even if validate is false', default=False )
    parser.add_argument( '-e', '--execute', action='store_true', help='actually execute the commands otherwise just print' )
    parser.add_argument( '-v', '--verbose', action='store_true', help='verbose output' )
    parser.add_argument( '-t', '--difftool', choices=['auto','vimdiff','ksdiff'], default='auto' )
    parser.add_argument( '--nogit', action='store_true', help='always search files without git', default=False )
    parser.add_argument( 'files',    metavar='FILES', nargs='*' )
    args = parser.parse_args()

    command = Config(args)

    if args.command in commands:
        getattr(command,commands[args.command]['attr'])()
    else:
        print( 'Invalid command "{}"'.format( args.command) )
        parser.print_help()
