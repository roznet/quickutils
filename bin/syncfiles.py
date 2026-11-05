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
"""
Synchronize configuration files between a git-backed repository and their
locations on the local filesystem, based on rules defined in a `.syncfiles`
JSON file.

The `.syncfiles` file is searched from the current working directory upwards
and describes how directories in this repository map to real source
directories, along with optional ignore patterns and a `validate` expression.

Commands implemented by this script let you:

- inspect differences (`status`, `diff`, `difftool`)
- bootstrap a machine (`install`)
- keep files in sync in either direction (`sync`, `pull`, `push`)
- check that encrypted files are really encrypted (`check`)

The `validate` expression, if present, is evaluated as a trusted Python
expression in a restricted environment with access to `os` and `platform`.
Use it to enable/disable a given configuration on a particular machine or OS.

Files listed in the optional `encrypted` map are stored sops-encrypted in the
repository. They are decrypted when copied to their destination and
re-encrypted when pulled back. Plaintext is never printed: it only travels
through pipes and the destination file (mode 0600 by default).
"""

import sys
import subprocess
import os
import hashlib
import argparse
import json
from pprint import pprint
import shutil
import fnmatch
import platform
import tempfile
import shlex
try:
    import grp
except ImportError:  # pragma: no cover - not available on Windows
    grp = None

def file_hash(filepath):
    """
    Compute a SHA1 hash for the file at ``filepath`` using a streaming reader.

    This avoids loading the whole file in memory, which is important for
    large configuration files.
    """
    sha1 = hashlib.sha1()
    with open(filepath, 'rb') as opened_file:
        for chunk in iter(lambda: opened_file.read(8192), b''):
            sha1.update(chunk)
    return sha1.hexdigest()


def current_umask():
    mask = os.umask(0)
    os.umask(mask)
    return mask


def group_id(group):
    if group is None:
        return -1
    if grp is None:
        raise OSError('group ownership is not supported on this platform')
    try:
        return grp.getgrnam(group).gr_gid
    except KeyError:
        raise OSError('unknown group "{}"'.format(group))


def write_atomic(path, data, mode, group=None):
    '''
    Write ``data`` (bytes) to ``path`` atomically: a temp file in the same
    directory, created with mode 0600 so plaintext is never exposed, then
    fsync, chgrp/chmod and rename over the destination.
    '''
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    gid = group_id(group)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.' + os.path.basename(path) + '.', suffix='.syncfiles-tmp')
    try:
        with os.fdopen(fd, 'wb') as opened:
            opened.write(data)
            opened.flush()
            os.fsync(opened.fileno())
            if gid != -1:
                os.fchown(opened.fileno(), -1, gid)
            os.fchmod(opened.fileno(), mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


SOPS_TYPES = ('dotenv', 'json', 'yaml', 'ini', 'binary')


class SopsError(Exception):
    '''A sops command failed, or its result did not pass the safety checks.'''


class NoKeyError(SopsError):
    '''None of the keys available on this host can decrypt the file.'''


class SopsSpec:
    '''
    How one encrypted file is handled, from its ``encrypted`` entry: either a
    type string (``"dotenv"``) or ``{"type": "json", "mode": "0640", "group": "www-data"}``.
    '''

    def __init__(self, sops_type, mode=0o600, group=None):
        self.type = sops_type
        self.mode = mode
        self.group = group

    @classmethod
    def parse(cls, rel_path, value):
        if isinstance(value, str):
            value = {'type': value}
        if not isinstance(value, dict):
            raise ValueError('encrypted entry "{}" must be a type string or an object'.format(rel_path))
        sops_type = value.get('type')
        if sops_type not in SOPS_TYPES:
            raise ValueError('encrypted entry "{}": type must be one of {}'.format(rel_path, ', '.join(SOPS_TYPES)))
        mode = value.get('mode', '0600')
        if not isinstance(mode, str):
            raise ValueError('encrypted entry "{}": mode must be an octal string such as "0640"'.format(rel_path))
        try:
            mode = int(mode, 8)
        except ValueError:
            raise ValueError('encrypted entry "{}": invalid mode "{}"'.format(rel_path, mode))
        return cls(sops_type, mode, value.get('group'))


class Sops:
    '''
    Thin wrapper over the ``sops`` CLI. Decrypted output is only captured from
    stdout and handed back to the caller; it is never printed.
    '''
    NO_KEY_EXIT = 128
    UNCHANGED_EXIT = 200

    def __init__(self, config_path=None):
        self.config_path = config_path

    def _cmd(self, *args):
        cmd = ['sops']
        if self.config_path:
            # --config is a global flag: it must come before the subcommand
            cmd += ['--config', self.config_path]
        return cmd + list(args)

    @staticmethod
    def _first_line(stderr):
        for line in stderr.decode('utf-8', 'replace').splitlines():
            if line.strip():
                return line.strip()
        return 'no error output'

    def decrypt(self, path, sops_type):
        out = subprocess.run(self._cmd('decrypt', '--input-type', sops_type, '--output-type', sops_type, path),
                             capture_output=True, stdin=subprocess.DEVNULL)
        if out.returncode == self.NO_KEY_EXIT:
            raise NoKeyError(self._first_line(out.stderr))
        if out.returncode != 0:
            raise SopsError('sops decrypt failed ({}): {}'.format(out.returncode, self._first_line(out.stderr)))
        return out.stdout

    def is_encrypted(self, path, sops_type):
        out = subprocess.run(['sops', 'filestatus', '--input-type', sops_type, path],
                             capture_output=True, stdin=subprocess.DEVNULL)
        if out.returncode != 0:
            # a plaintext binary, or anything that doesn't parse as a sops file
            return False
        try:
            return json.loads(out.stdout.decode('utf-8')).get('encrypted') is True
        except ValueError:
            return False

    def encrypt_new(self, plaintext_path, repo_path, sops_type):
        '''
        Encrypt ``plaintext_path`` using the creation rule for ``repo_path``
        and return the ciphertext. The absolute repo path is passed, because
        sops resolves the name against the current directory and matches
        path_regex relative to the directory holding .sops.yaml.
        '''
        if not self.config_path:
            raise SopsError('no .sops.yaml found between the .syncfiles directory and the git root')
        out = subprocess.run(self._cmd('encrypt', '--filename-override', os.path.abspath(repo_path),
                                       '--input-type', sops_type, '--output-type', sops_type, plaintext_path),
                             capture_output=True, stdin=subprocess.DEVNULL)
        if out.returncode != 0:
            raise SopsError('sops encrypt failed ({}): {}'.format(out.returncode, self._first_line(out.stderr)))
        return out.stdout

    def edit_from(self, encrypted_path, plaintext_path, sops_type):
        '''
        Replace, in place, the content of ``encrypted_path`` with the plaintext
        file, through ``sops edit``, so values that did not change keep their
        ciphertext and git diffs stay small. Returns False if nothing changed.
        '''
        tmp_root = tempfile.gettempdir()
        if ' ' in tmp_root:
            tmp_root = '/tmp'  # sops splits $SOPS_EDITOR on spaces
        helper_dir = tempfile.mkdtemp(prefix='syncfiles-sops-', dir=tmp_root)
        helper = os.path.join(helper_dir, 'editor.sh')
        try:
            with open(helper, 'w') as opened:
                opened.write('#!/bin/sh\nexec cp {} "$1"\n'.format(shlex.quote(os.path.abspath(plaintext_path))))
            os.chmod(helper, 0o700)
            # both: SOPS_EDITOR wins over EDITOR, and the owner sets SOPS_EDITOR
            env = dict(os.environ, SOPS_EDITOR=helper, EDITOR=helper)
            out = subprocess.run(self._cmd('edit', '--input-type', sops_type, '--output-type', sops_type, encrypted_path),
                                 capture_output=True, env=env, stdin=subprocess.DEVNULL)
        finally:
            shutil.rmtree(helper_dir, ignore_errors=True)
        if out.returncode == self.UNCHANGED_EXIT:
            return False
        if out.returncode == self.NO_KEY_EXIT:
            raise NoKeyError(self._first_line(out.stderr))
        if out.returncode != 0:
            raise SopsError('sops edit failed ({}): {}'.format(out.returncode, self._first_line(out.stderr)))
        return True


class FilePair :
    """
    Represent a logical pair of files: one in the repository tree (``dir``)
    and one in the source filesystem (``src``), identified by a relative path.

    For example:
        dir:      /repo/config/etc/apache2
        src:      /etc/apache2
        relpath:  sites/default.conf

    When ``sops_spec`` is set, the repository file is sops-encrypted and is
    compared and copied through its decrypted content.
    """

    def __init__(self,dir,src_dir,file_rel_path,sops_spec=None,sops=None):
        '''
        FilePair Object:

        dir: directory where the file is stored in git/sync repo (ex: /path/to/syncfiles/etc/apache2)
        src_dir: directory where the file is supposed to be (ex: /etc/apache2)
        file_rel_path: rel path inside dir and src_dir (ex: sites/default.conf )
        sops_spec: SopsSpec if the repo file is encrypted, None otherwise
        sops: Sops runner used for encrypted files
        '''
        self.dir = dir
        self.src = src_dir
        self.file_rel_path = file_rel_path
        self.sops_spec = sops_spec
        self.sops = sops
        self._plaintext = None
        self._sops_state = None
        self.sops_message = None

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

    def is_encrypted(self):
        return self.sops_spec is not None

    def sops_state(self):
        '''
        For encrypted pairs, one of:
          ok        decrypts on this host
          missing   not in the repo yet
          plaintext listed as encrypted but stored in plaintext (refuse everything)
          nokey     this host has no key for it
          error     another sops failure (bad MAC, corrupt file...)
        '''
        if not self.is_encrypted():
            return None
        if self._sops_state is None:
            if not self.dir_file_exists():
                self._sops_state = 'missing'
            elif not self.sops.is_encrypted(self.dir_file(), self.sops_spec.type):
                self._sops_state = 'plaintext'
            else:
                try:
                    self._plaintext = self.sops.decrypt(self.dir_file(), self.sops_spec.type)
                    self._sops_state = 'ok'
                except NoKeyError as err:
                    self._sops_state = 'nokey'
                    self.sops_message = str(err)
                except SopsError as err:
                    self._sops_state = 'error'
                    self.sops_message = str(err)
        return self._sops_state

    def reset(self):
        self._plaintext = None
        self._sops_state = None
        self.sops_message = None

    def dir_content(self):
        '''
        The repo-side plaintext bytes. For encrypted files this is the
        decrypted content: never write it anywhere but the destination.
        '''
        if not self.is_encrypted():
            with open(self.dir_file(), 'rb') as opened:
                return opened.read()
        state = self.sops_state()
        if state == 'nokey':
            raise NoKeyError(self.sops_message)
        if state != 'ok':
            raise SopsError('{}: {}'.format(self.dir_file(), self.sops_message or state))
        return self._plaintext

    def is_match(self):
        if not self.src_is_readable() or not self.dir_is_readable():
            return False
        if self.is_encrypted():
            if self.sops_state() != 'ok':
                return False
            with open(self.src_file(), 'rb') as opened:
                return opened.read() == self._plaintext
        return file_hash( self.dir_file() ) == file_hash( self.src_file() )

    def src_mode_ok(self):
        '''Encrypted pairs only: the destination has the expected mode (and group).'''
        if not self.is_encrypted() or not self.src_file_exists():
            return True
        st = os.stat(self.src_file())
        if (st.st_mode & 0o7777) != self.sops_spec.mode:
            return False
        if self.sops_spec.group is not None:
            try:
                return st.st_gid == group_id(self.sops_spec.group)
            except OSError:
                return False
        return True

    def src_is_readable(self):
        return os.access( self.src_file(), os.R_OK )
    def dir_is_readable(self):
        return os.access( self.dir_file(), os.R_OK )


class Config :
    """
    Configuration and command dispatcher for ``syncfiles.py``.

    Responsible for:
    - locating and loading the nearest ``.syncfiles`` file
    - expanding directory mappings and ignore rules
    - constructing ``FilePair`` instances
    - executing high-level commands (status, sync, install, etc.)
    """

    def __init__(self,args=None):
        self.args = args
        self.verbose = self.args.verbose
        self.show_secrets = getattr(self.args, 'show_secrets', False)
        self.errors = False
        self.cwd = os.getcwd()
        self.find_config_file()
        self.expand_dirs()
        self.setup_encryption()
        self.max_len_dir = 0
        self.max_len_src = 0

    def find_config_file(self):
        '''
        Find the config file in the current directory or any parent directory.
        Also detects the git root (unless ``--nogit`` is passed).
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
            print( "Couldn't locate a .syncfiles" )
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
            expr = self.defs['validate']
            env = {'__builtins__': {}, 'os': os, 'platform': platform}
            try:
                valid = bool(eval( expr, env, {} ))
            except Exception as exc:
                if not self.args.force:
                    print( 'Validation "{}" raised {}: {}'.format( expr, type(exc).__name__, exc ) )
                    sys.exit(1)
                else:
                    if self.verbose:
                        print( 'WARNING: validation "{}" raised {}, continuing because --force is set'.format( expr, type(exc).__name__ ) )
                    valid = True
            else:
                if not valid:
                    if not self.args.force:
                        print( 'Skipping, Validation "{}" evaluates to False'.format( expr ) )
                        sys.stdout.flush()
                        sys.exit(0)
                    else:
                        if self.verbose:
                            print( 'WARNING: validation "{}" evaluates to False, continuing because --force is set'.format( expr ) )
                else:
                    if self.verbose:
                        print( 'CONTINUE: Validation "{}" evaluates to True'.format( expr ) );

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

        # Always ignore syncfiles and the sops rules
        self.ignore_map = {os.path.join( self.basedir, '.syncfiles' ):1, os.path.join( self.basedir, '.sops.yaml' ):1}
        self.ignore_pattern = {}
        if 'ignore' in self.defs:
            for dir in self.defs['ignore']:
                to_ignore = os.path.join( self.basedir, dir )
                if os.path.exists( to_ignore ):
                    self.ignore_map[to_ignore] = 1
                else:
                    self.ignore_pattern[ dir ] = 1

    def setup_encryption(self):
        '''
        Parse the optional ``encrypted`` map (repo path relative to the
        .syncfiles directory -> sops type or spec) and locate .sops.yaml.
        '''
        self.encrypted = {}
        self.sops = None
        entries = self.defs.get('encrypted', {})
        if not isinstance(entries, dict):
            print( 'Invalid .syncfiles: "encrypted" must be a map of path -> sops type' )
            sys.exit(1)
        for rel_path, value in entries.items():
            try:
                spec = SopsSpec.parse(rel_path, value)
            except ValueError as err:
                print( 'Invalid .syncfiles: {}'.format(err) )
                sys.exit(1)
            self.encrypted[os.path.normpath(os.path.join(self.basedir, rel_path))] = spec

        if self.encrypted:
            if not shutil.which('sops'):
                print( 'ERROR: .syncfiles lists encrypted files but sops is not installed (or not on PATH)' )
                sys.exit(1)
            self.sops = Sops(self.find_sops_config())
            if self.verbose:
                print( 'CONFIG: {} encrypted files, sops config={}'.format(len(self.encrypted), self.sops.config_path) )

    def find_sops_config(self):
        '''
        Walk up from the .syncfiles directory to the git root (or / without
        git) looking for .sops.yaml.
        '''
        stop = os.path.realpath(self.git_root) if self.git_root else '/'
        current = os.path.realpath(self.basedir)
        while True:
            candidate = os.path.join(current, '.sops.yaml')
            if os.path.isfile(candidate):
                return candidate
            if current == stop or current == os.path.dirname(current):
                return None
            current = os.path.dirname(current)

    def make_pair(self, dir, src, rel_path):
        spec = self.encrypted.get(os.path.normpath(os.path.join(dir, rel_path)))
        pair = FilePair(dir, src, rel_path, spec, self.sops if spec else None)
        self.max_len_dir = max(self.max_len_dir, len(self.display_dir_file(pair)))
        self.max_len_src = max(self.max_len_src, len(self.display_src_file(pair)))
        return pair

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

                            rv += [ self.make_pair( dir_path, src, rel_path ) ]

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
                        rv += [ self.make_pair( dir, src_path, rel_path ) ]

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
                        pair = self.make_pair( dir, src, rel_file )
                        if self.verbose:
                            print( 'FOUND: discovered %s'%(pair,) )
                        file_pairs += [ pair ]
        return file_pairs


    def construct_git_pairs(self):
        file_pairs = []
        cmd = [ 'git', 'ls-files' ]
        if self.args.files:
            cmd += self.args.files
        git_ls_out = subprocess.run( cmd, capture_output=True)
        if git_ls_out.returncode != 0:
            if self.verbose:
                print( 'CONFIG: git ls-files failed with return code {}'.format( git_ls_out.returncode ) )
            return file_pairs

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

                pair = self.make_pair(found_dir, self.expand_dir_to_src[found_dir], file_rel_path)
                file_pairs += [ pair ]
                if self.verbose:
                    print( 'FOUND: from git {}'.format(pair) )

        return file_pairs

    def pair_for_repo_path(self, full_path):
        '''FilePair for an absolute repo path, using the longest matching dirmap entry.'''
        found_dir = None
        for repo_dir in self.expand_dir_to_src:
            if full_path.startswith( repo_dir + '/' ) and ( found_dir is None or len(found_dir) < len(repo_dir) ):
                found_dir = repo_dir
        if found_dir is None:
            return None
        return self.make_pair(found_dir, self.expand_dir_to_src[found_dir], full_path[len(found_dir)+1:])

    def in_files_filter(self, full_path):
        if not self.args or not self.args.files:
            return True
        for one in self.args.files:
            wanted = os.path.normpath(os.path.join(self.cwd, one))
            if full_path == wanted or full_path.startswith(wanted + '/'):
                return True
        return False

    def declared_encrypted_pairs(self, known):
        '''
        Encrypted entries are pairs even when the repo file does not exist
        yet (so ``pull`` can create it) or is not tracked by git yet.
        '''
        have = set( os.path.normpath(x.dir_file()) for x in known )
        rv = []
        for full_path in sorted(self.encrypted):
            if full_path in have or not self.in_files_filter(full_path):
                continue
            pair = self.pair_for_repo_path(full_path)
            if pair is None:
                print( 'WARNING: encrypted entry {} is not under any dirmap entry'.format(os.path.relpath(full_path, self.basedir)) )
                continue
            if self.should_ignore(full_path, pair.file_rel_path):
                continue
            rv.append(pair)
        return rv

    def list_file_pairs(self):
        if self.git_root:
            pairs = self.construct_git_pairs()
        elif self.args and self.args.files:
            pairs = self.construct_file_pairs(self.args.files)
        else:
            pairs = self.discover_file_pairs()
        if self.encrypted:
            pairs += self.declared_encrypted_pairs(pairs)
        return pairs

    def _existing_pairs(self):
        """
        Helper returning all file pairs for which the source file exists.
        """
        return [x for x in self.list_file_pairs() if x.src_file_exists() ]

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
        state = filepair.sops_state()
        if state == 'plaintext':
            self.errors = True
            return 'X %s   %s   NOT ENCRYPTED in the repo: refusing to sync it' %(self.display_dir_file(filepair), self.display_src_file(filepair) )
        if state == 'error':
            self.errors = True
            return 'E %s   %s   %s' %(self.display_dir_file(filepair), self.display_src_file(filepair), filepair.sops_message )
        if state == 'nokey':
            return 'K %s   %s' %(self.display_dir_file(filepair), self.display_src_file(filepair) )

        if filepair.src_file_exists():
            if not filepair.src_is_readable():
                return '! %s   %s' %(self.display_dir_file(filepair), self.display_src_file(filepair) )
            elif not filepair.dir_is_readable():
                return 'A %s   %s' %(self.display_dir_file(filepair), self.display_src_file(filepair) )
            else:
                if filepair.is_match():
                    if not filepair.src_mode_ok():
                        return 'M %s = %s   (mode %s, expected %s)' %(self.display_dir_file(filepair), self.display_src_file(filepair),
                                                                     oct(os.stat(filepair.src_file()).st_mode & 0o7777), oct(filepair.sops_spec.mode) )
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
        '''
        Synchronize files in both directions based on modification time.

        For each existing source file, copy the newer of the repo and source
        versions to the older one.
        '''
        exists = self._existing_pairs()

        for x in exists:
            if not x.is_match():
                if not x.src_is_readable():
                    print( 'cannot read: %s' % (x.src_file(),) )
                elif x.is_encrypted() and not x.dir_file_exists():
                    self.to_dir(x)
                elif not x.dir_is_readable():
                    print( 'cannot read: %s' %(x.dir_file(),))
                else:
                    m_src = os.path.getmtime( x.src_file() )
                    m_dir = os.path.getmtime( x.dir_file() )
                    if m_dir < m_src:
                        self.to_dir(x)
                    else:
                        self.to_src(x)
            elif not x.src_mode_ok():
                self.to_src(x)

        self.execute_warning()

    def cmd_pull(self):
        '''
        Copy files from their source locations into the repository.

        Only mismatched existing files are copied, from source to repo.
        '''
        exists = self._existing_pairs()

        for x in exists:
            if not x.is_match():
                if not x.src_is_readable():
                    print( 'cannot read: %s' % (x.src_file(),) )
                elif x.is_encrypted() and not x.dir_file_exists():
                    self.to_dir(x)
                elif not x.dir_is_readable():
                    print( 'cannot read: %s' %(x.dir_file(),))
                else:
                    self.to_dir(x)

        self.execute_warning()


    def cmd_push(self):
        '''
        Copy files from the repository into their source locations.

        Only mismatched existing files are copied, from repo to source.
        '''
        exists = self._existing_pairs()

        for x in exists:
            if not x.is_match() or not x.src_mode_ok():
                self.to_src(x)

        self.execute_warning()

    def to_src(self, pair):
        '''Repo -> destination (install, push, repo-newer half of sync).'''
        if not pair.is_encrypted():
            self.copyfile(pair.dir_file(), pair.src_file())
            return
        if not self.encrypted_ok_to_copy(pair):
            return
        print( 'decrypt {} -> {}'.format(pair.dir_file(), pair.src_file()) )
        if self.args.execute:
            try:
                write_atomic(pair.src_file(), pair.dir_content(), pair.sops_spec.mode, pair.sops_spec.group)
            except (OSError, SopsError) as err:
                self.errors = True
                print( 'ERROR: could not install {}: {}'.format(pair.src_file(), err) )

    def to_dir(self, pair):
        '''Destination -> repo (pull, dest-newer half of sync).'''
        if not pair.is_encrypted():
            self.copyfile(pair.src_file(), pair.dir_file())
            return
        if pair.sops_state() != 'missing' and not self.encrypted_ok_to_copy(pair):
            return
        print( 'encrypt {} -> {}'.format(pair.src_file(), pair.dir_file()) )
        if self.args.execute:
            try:
                self.encrypt_into_repo(pair)
            except (OSError, SopsError) as err:
                self.errors = True
                print( 'ERROR: {} left untouched: {}'.format(pair.dir_file(), err) )

    def encrypted_ok_to_copy(self, pair):
        state = pair.sops_state()
        if state == 'ok':
            return True
        if state == 'missing':
            print( 'skip (not in the repo yet, run pull): {}'.format(pair.dir_file()) )
        elif state == 'nokey':
            print( 'skip (no key on this host): {}'.format(pair.dir_file()) )
        elif state == 'plaintext':
            self.errors = True
            print( 'REFUSING: {} is listed as encrypted but is stored in plaintext'.format(pair.dir_file()) )
        else:
            self.errors = True
            print( 'REFUSING: {}: {}'.format(pair.dir_file(), pair.sops_message) )
        return False

    def encrypt_into_repo(self, pair):
        '''
        Fail closed: the new ciphertext is built in a temp file next to the
        repo file, checked, and only then renamed over it.
        '''
        repo_file = pair.dir_file()
        src_file = pair.src_file()
        sops_type = pair.sops_spec.type
        directory = os.path.dirname(repo_file)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix='.' + os.path.basename(repo_file) + '.', suffix='.syncfiles-tmp')
        os.close(fd)
        try:
            if pair.dir_file_exists():
                shutil.copyfile(repo_file, tmp)
                shutil.copymode(repo_file, tmp)
                if not self.sops.edit_from(tmp, src_file, sops_type):
                    print( '  unchanged: {}'.format(repo_file) )
                    os.unlink(tmp)
                    return
            else:
                ciphertext = self.sops.encrypt_new(src_file, repo_file, sops_type)
                with open(tmp, 'wb') as opened:
                    opened.write(ciphertext)
                os.chmod(tmp, 0o666 & ~current_umask())
            if not self.sops.is_encrypted(tmp, sops_type):
                raise SopsError('sops output did not pass filestatus, not writing it')
            os.replace(tmp, repo_file)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        pair.reset()
        # Round-trip check: sops re-serialises (drops blank lines, reformats
        # JSON...). Compare plaintext to plaintext, never ciphertext.
        try:
            with open(src_file, 'rb') as opened:
                if pair.dir_content() != opened.read():
                    print( '  note: {} decrypts to a normalised form; run install once so the destination matches'.format(repo_file) )
        except SopsError:
            pass

    def copyfile(self,from_path,to_path):
        if self.args.execute:
            print( 'cp {} {}'.format(from_path, to_path ) )
            try:
                shutil.copyfile( from_path, to_path )
            except IOError as io_err:
                to_dir = os.path.dirname( to_path )
                os.makedirs( to_dir, exist_ok=True )
                shutil.copyfile( from_path, to_path )
        else:
            print( 'cp {} {}'.format(from_path, to_path ) )

    def execute_warning(self):
        if not self.args.execute:
            print()
            print( 'Preview mode. Files were not copied, add --execute option to copy' )

    def cmd_install(self):
        '''
        Ensure that every repository file exists at its source location.

        For any missing or mismatched source file, copy from repo to source.
        '''
        for x in self.list_file_pairs():
            if not x.src_file_exists() or not x.is_match() or not x.src_mode_ok():
                self.to_src(x)

        self.execute_warning()

    def cmd_status(self):
        '''
        Print the status of all known file pairs, including:
        - identical files (``.``)
        - modified files (``M``)
        - unreadable files (``!`` / ``A``)
        - files missing from the source side (``?``)
        - encrypted files: no key on this host (``K``), stored in plaintext
          (``X``), other sops errors (``E``)
        '''
        file_pairs = self.list_file_pairs()
        exists = [ x for x in file_pairs if x.src_file_exists() ]
        unknown = [ x for x in file_pairs if not x.src_file_exists() ]

        done = False
        for x in exists:
            if self.output_status(x):
                done = True;

        for x in unknown:
            if self.output_status(x):
                done = True;
        if not done and not self.args.all:
            print( 'All up to date' )

    def cmd_check(self):
        '''
        Check that every file listed in ``encrypted`` is stored encrypted.
        Needs no key, so it works in a pre-commit hook on any machine.
        '''
        bad = 0
        for full_path, spec in sorted(self.encrypted.items()):
            rel = os.path.relpath(full_path, self.basedir)
            if not os.path.isfile(full_path):
                if self.verbose:
                    print( '- {} (not in the repo yet)'.format(rel) )
                continue
            if self.sops.is_encrypted(full_path, spec.type):
                if self.args.all:
                    print( '. {}'.format(rel) )
            else:
                bad += 1
                print( 'X {}   NOT ENCRYPTED'.format(rel) )
        if bad:
            self.errors = True
        elif self.verbose or self.args.all:
            print( 'All {} encrypted files are encrypted'.format(len(self.encrypted)) )

    def cmd_apply(self,fns):
        '''
        Apply a callable to each existing file pair.

        This is a low-level helper used by other commands.
        '''
        exists = self._existing_pairs()
        for x in exists:
            fns(x)

    def decrypted_temp(self, pair):
        '''
        For diff/difftool on an encrypted pair: only with --show-secrets, write
        the decrypted repo side to a mode-600 temp file and return its path.
        '''
        if not self.show_secrets:
            print( '  skipped: encrypted, diff would print secrets (add --show-secrets)' )
            return None
        if pair.sops_state() != 'ok':
            return None
        fd, tmp = tempfile.mkstemp(prefix='syncfiles-', suffix='-' + os.path.basename(pair.file_rel_path))
        with os.fdopen(fd, 'wb') as opened:
            opened.write(pair.dir_content())
        return tmp

    def cmd_difftool(self):
        tool = None
        if self.args.difftool == 'auto':
            if shutil.which( 'ksdiff' ):
                tool = ['ksdiff', '--partial-changeset']
            elif shutil.which( 'icdiff' ):
                tool = ['icdiff']
            else:
                tool = ['vimdiff']
        elif self.args.difftool.startswith( 'k' ):
                tool = ['ksdiff', '--partial-changeset']
        elif self.args.difftool.startswith( 'i'):
                tool = ['icdiff']
        elif self.args.difftool.startswith( 'v' ):
                tool = ['vimdiff']

        exists = self._existing_pairs()
        for x in exists:
            if not x.is_match():
                self.output_status(x)
                if x.is_encrypted():
                    tmp = self.decrypted_temp(x)
                    if tmp:
                        try:
                            # ksdiff returns at once: wait for the user before deleting the plaintext
                            subprocess.call( tool + [ tmp, x.src_file() ] )
                            if tool[0] == 'ksdiff':
                                input( '  press return when done to delete the decrypted temp file ' )
                        finally:
                            os.unlink(tmp)
                    continue
                process_arg = tool + [ x.dir_file(), x.src_file() ]

                subprocess.call( process_arg )

    def cmd_diff(self):
        '''
        Show ``diff`` output for each mismatched existing file pair.
        '''
        exists = self._existing_pairs()

        for x in exists:
            if not x.src_is_readable():
                continue
            if not x.is_match():
                self.output_status(x)
                if x.is_encrypted():
                    tmp = self.decrypted_temp(x)
                    if tmp:
                        try:
                            subprocess.call( [ 'diff', tmp, x.src_file() ] )
                        finally:
                            os.unlink(tmp)
                    continue
                subprocess.call( [ 'diff', x.dir_file(), x.src_file() ]  )

if __name__ == "__main__":

    commands = {
        'status':{'attr':'cmd_status','help':'Show status of files'},
        'install':{'attr':'cmd_install','help':'Copy all the missing files to source'},
        'diff':{'attr':'cmd_diff','help':'Show diff for modified files'},
        'difftool':{'attr':'cmd_difftool','help':'Show diff for modified files in ksdiff or vimdiff'},
        'sync':{'attr':'cmd_sync','help':'copy most recent files to older one'},
        'pull':{'attr':'cmd_pull','help':'copy to local the original files'},
        'push':{'attr':'cmd_push','help':'push local file to the original location'},
        'check':{'attr':'cmd_check','help':'check that files listed in "encrypted" are encrypted (no key needed)'}
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
    parser.add_argument( '--show-secrets', action='store_true', help='allow diff/difftool on encrypted files (prints plaintext)', default=False )
    parser.add_argument( 'files',    metavar='FILES', nargs='*' )
    # intermixed: flags may come after FILES too (`install -e bin etc`), which plain
    # parse_args rejects with "unrecognized arguments" because of the nargs="*" positional
    args = parser.parse_intermixed_args()

    command = Config(args)

    if args.command in commands:
        getattr(command,commands[args.command]['attr'])()
        sys.exit( 1 if command.errors else 0 )
    else:
        print( 'Invalid command "{}"'.format( args.command) )
        parser.print_help()
