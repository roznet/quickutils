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

import argparse
import os
import re
import pprint
from collections import defaultdict

re_translation = re.compile(r'^"(.+)" = "(.+)"; *(/\*.*\*/)?$')
re_comment_single = re.compile(r'^/\*.*\*/$')
re_comment_start = re.compile(r'^/\*.*')
re_comment_end = re.compile(r'.*\*/$')

class LocalizationEntry:

    def from_other_with_attr( other, attr = '' ):
        rv = LocalizationEntry()
        rv.comment = other.comment
        rv.localization = other.localization
        rv.key, rv.translation, rv.attr = (other.key, other.translation, attr )

        return rv
        
    
    def from_lines(comment,localization):
        rv = LocalizationEntry()
        rv.comment = comment
        rv.localization = localization
        rv.key, rv.translation, rv.attr = re_translation.match(localization).groups()

        return rv

    def is_tranlated(self):
        return self.key != self.translation
    
    def comment_str(self):
        return '\n'.join( self.comment )

    def write_to_file(self,fh):
        attr = ' ' + self.attr if self.attr else ''
        fh.write( '"{}" = "{}";{}\n'.format( self.key, self.translation, attr ) ) 

    def __repr__(self):
        return '<LocalizationEntry: {} {}>'.format( self.comment, self.localization )

class Localizations:
    def __init__(self):
        self.localizations = []
        self.comments = defaultdict(list)
        self.translations = defaultdict(list)
        self.translated = {}
        self.added = {}
        self.commentchange = {}
        self.commentadded = {}
        
    def add_localization(self,entry):
        self.localizations.append(entry)
        self.comments[entry.comment_str()].append( entry )
        self.translations[entry.key].append( entry )
        if len(self.translations[entry.key]) > 1:
            print( '{} has {} entries'.format( entry.key, len( self.translations[entry.key] ) ) )
        if entry.is_tranlated():
            self.translated[entry.key] = entry

    def rebuild(self):
        rebuilt = Localizations()
        for one in self.localizations:
            rebuilt.add_localization( one )
        
        self.localizations = rebuilt.localizations
        self.comments = rebuilt.comments
        self.translations = rebuilt.translations
        self.translated = rebuilt.translated
            
    def add_missing(self,other):
        missing = defaultdict(list)

        for entry in other.localizations:
            if entry.key not in self.translations:
                missing[entry.key].append( entry )
                if entry.comment_str() not in self.comments:
                    self.commentadded[entry.comment_str()] = entry
            else:
                existing = self.translations[entry.key][0]
                if existing.comment_str() != entry.comment_str():
                    self.commentchange[entry.key] = existing
                    existing.comment = entry.comment

        for (key,entries) in missing.items():
            if len(entries) != 1:
                print( 'ERROR: {} has {} entries'.format( key, len(entries) ) )
            self.add_localization( LocalizationEntry.from_other_with_attr( entries[0], '/* NEW */' ) )
            self.added[key] = entries[0]

        self.rebuild()

    def info(self):
        return {'comments':len(self.comments),'keys':len(self.translations) }

    def describe(self):
        if len( self.translated ):
            return '{}/{} keys translated for {} comments'.format( len(self.translated), len(self.translations), len(self.comments) )
        else:
            return '{} keys for {} comments'.format( len(self.translations), len(self.comments) )

    def describe_change(self):
        msgs = [ '{} keys'.format( len(self.translations ) )]
        if len( self.added ):
            msgs.append( '{} added'.format( len(self.added) ) )
        else:
            msgs.append( 'unchanged' )
            
        msgs.append( '{} comments'.format( len(self.comments) ) )
        changed = False
        if len(self.commentadded):
            msgs.append( '{} added'.format( len(self.commentadded) ) )
            changed = True
        if len(self.commentchange):
            msgs.append( '{} changed'.format( len(self.commentchange) ) )
            changed = True
        if not changed:
            msgs.append( 'unchanged' )
        return ' '.join(msgs )

        
    def write_to_file(self,fh):
        donekey = {}

        for (comment,entries) in sorted(self.comments.items()):
            commentmissing = True
            for entry in entries:
                if commentmissing:
                    fh.write( entry.comment_str() )
                    fh.write( '\n' )
                    commentmissing = False
                if entry.key in donekey:
                    print( 'ERROR: {} already done {}'.format( entry.key, donekey[entry.key] ) )
                donekey[entry.key] = entry
                entry.write_to_file(fh)
            fh.write( '\n')
    
    def __repr__(self):
        return '<LocalizationEntry: {}>'.format( self.localizations )
        
class Driver:
    def __init__(self,args):
        self.args = args
        
    def cmd_build(self):
        self.run_genstrings()
        base = self.read_strings( 'base.lproj' )

        dirs = os.listdir( '.' )
        for dir in dirs:
            if dir.endswith( '.lproj' ) and len(dir) == len('en.lproj' ):
                if self.args.save:
                    fn = 'Localizable.strings'
                else:
                    fn = 'Localizable-new.strings'
                
                en = self.read_strings(dir, 'Localizable.strings' )
                print( f'Read {dir} {en.describe()}' )
                en.add_missing( base )
                print( f'Merged base {en.describe_change()}' )
                fh = open( os.path.join(dir, fn ), 'w', encoding='utf8' )
                en.write_to_file(fh)
                print( f'Saved {dir}/{fn} {en.describe()}' )
        
    def run_genstrings(self,path='src'):
        os.system( "genstrings -q -o base.lproj $(find {} -name '*.m' -o -name '*.swift')".format( path ) )
        os.rename( 'base.lproj/Localizable.strings', 'base.lproj/Localizable.strings.utf16' )
        os.system( 'iconv -f UTF-16 -t UTF-8 "{}" > "{}"'.format( 'base.lproj/Localizable.strings.utf16', 'base.lproj/Localizable.strings' ) )
        
    def read_strings(self,dirname,fn='Localizable.strings'):
        fname = os.path.join(dirname,fn)
        f = open(fname, 'r')

        line = f.readline()
        localizations = Localizations()

        n = defaultdict(int)
        
        while line:
            # process comment
            comment = [line.rstrip()]
            n['comments'] += 1
            if not re_comment_single.match(line):
                while line and not re_comment_end.match(line):
                    line = f.readline()
                    comment.append(line.rstrip())
                    
            # process all locatiozations under comment
            line = f.readline()
            while line and re_translation.match(line):
                localizations.add_localization( LocalizationEntry.from_lines( comment, line.rstrip() ) )
                line = f.readline()
                n['keys'] += 1

            # skip all empty lines
            line = f.readline()
            while line and line == u'\n':
                line = f.readline()

        f.close()
        return localizations


if __name__ == "__main__":
                
    commands = {
        'build':{'attr':'cmd_build','help':'Rebuild database'},
    }

    description = "\n".join( [ '  {}: {}'.format( k,v['help'] ) for (k,v) in commands.items() ] )

    parser = argparse.ArgumentParser( description='Check configuration', formatter_class=argparse.RawTextHelpFormatter )
    parser.add_argument( 'command', metavar='Command', help='command to execute:\n' + description)
    parser.add_argument( '-c', '--clear', action='store_true', help='clear existing attributes' )
    parser.add_argument( '-s', '--save', action='store_true', help='save output otherwise just print' )
    parser.add_argument( '-o', '--output', help='output file' )
    parser.add_argument( '-v', '--verbose', action='store_true', help='verbose output' )
    parser.add_argument( 'files',    metavar='FILES', nargs='*', help='files to process' )
    args = parser.parse_args()

    command = Driver(args)

    if args.command in commands:
        getattr(command,commands[args.command]['attr'])()
    else:
        print( 'Invalid command "{}"'.format( args.command) )
        parser.print_help()

        
