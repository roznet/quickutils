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

re_translation = re.compile(r'^"(.+)" = "(.+)";$')
re_comment_single = re.compile(r'^/\*.*\*/$')
re_comment_start = re.compile(r'^/\*.*')
re_comment_end = re.compile(r'.*\*/$')

class LocalizationEntry:
    def __init__(self,comment,localization):
        self.comment = comment
        self.localization = localization
        self.key, self.translation = re_translation.match(localization).groups()
        
    def __repr__(self):
        return '<LocalizationEntry: {} {}>'.format( self.comment, self.localization )

class Localizations:
    def __init__(self):
        self.localizations = []
        
    def add_localization(self,entry):
        self.localizations.append(entry)

    def __repr__(self):
        return '<LocalizationEntry: {}>'.format( self.localizations )
        
class Driver:
    def __init__(self,args):
        self.args = args
        
    def cmd_build(self):
        self.run_genstrings()
        self.read_strings( 'base.lproj' )
        
    def run_genstrings(self,path='src'):
        os.system( "genstrings -o base.lproj $(find {} -name '*.m' -o -name '*.swift')".format( path ) )
        os.rename( 'base.lproj/Localizable.strings', 'base.lproj/Localizable.strings.utf16' )
        os.system( 'iconv -f UTF-16 -t UTF-8 "{}" > "{}"'.format( 'base.lproj/Localizable.strings.utf16', 'base.lproj/Localizable.strings' ) )
        
    def read_strings(self,dirname):
        fname = os.path.join(dirname,'Localizable.strings')
        f = open(fname, 'r')

        line = f.readline()
        localizations = Localizations()
        
        while line:
            # process comment
            comment = [line.rstrip()]
 
            if not re_comment_single.match(line):
                while line and not re_comment_end.match(line):
                    line = f.readline()
                    comment.append(line.rstrip())

            # process all locatiozations under comment
            line = f.readline()
            while line and re_translation.match(line):
                localizations.add_localization( LocalizationEntry( comment, line.rstrip() ) )
                line = f.readline()

            # skip all empty lines
            line = f.readline()
            while line and line == u'\n':
                line = f.readline()
 
        f.close()


if __name__ == "__main__":
                
    commands = {
        'build':{'attr':'cmd_build','help':'Rebuild database'},
    }

    description = "\n".join( [ '  {}: {}'.format( k,v['help'] ) for (k,v) in commands.items() ] )

    parser = argparse.ArgumentParser( description='Check configuration', formatter_class=argparse.RawTextHelpFormatter )
    parser.add_argument( 'command', metavar='Command', help='command to execute:\n' + description)
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

        
