# Quick Utils

This is just a collection of small utilities to facilitate management of my computer, network and development life

Not intended necessarily to be re-used as is, but more example of ad-hoc utilities

Here are the utilities:

* synchronize configuration files with a git repo using [`syncfiles.py`](#syncfilespy)
* find ios simulator files and simplify xcode upgrades from one version to the next, with the [`simctl.py`](#simctlpy) wrapper on `simctl`
* merge and update new translations with [`genstring.py`](#genstringpy), a wrapper on `genstrings`

# How to install:

You can use the syncfiles.py utility itself or copy manually what you need

```
mkdir ~/bin
./bin/syncfiles.py install
```


# syncfiles.py

syncfiles.py is intended to solve the issue that I wanted to keep all my config files in a git repository but the config files can be spread into many different location in the system and it isn't always easy to make that location a git repo as there would be a lot of files to ignore. Also I wanted a single repo containing everything, so if I needed to restore the machine from script, a single `git clone` and `syncfiles.py install` would set up everything.

## Setup

This utilities can be used to synchronize files between different location in the system or home directory and a github repository. I use it to keep all my config files both for $HOME and the sytem config (/etc, /lib, etc)

It relies on a json file .syncfiles that contains the configuration of the map between the source and the repo. The file has two keys `dirmap` and `ignore`. Here is an example:

```
{"dirmap":{".":"~/","etc":"/etc"},"ignore":["README.md","*~"]}
```

In this example all the files in `.` will be sync'd to your home dir and the files in `etc` will be sync'd to `/etc`. The files `README.md` or the files matching the pattern `*~` will be ignored. The files in the dirmap, for example here `etc` are automatically ignored for the current directory rule `.`.

## Usage

The `syncfiles.py` should always be run from within the repo directory where the .syncfiles is (or one of its subdir). The utility takes a command as an argument, here is the usage:

```
usage: syncfiles.py [-h] [-e] [-v] Command [FILES [FILES ...]]

Check configuration

positional arguments:
  Command        command to execute:
                   status: Show status of files
                   install: Copy all the missing files to source
                   diff: Show diff for modified files
                   difftool: Show diff for modified files in ksdiff
                   sync: copy most recent files to older one
                   pull: copy to local the original files
                   push: push local file to the original location
  FILES

optional arguments:
  -h, --help     show this help message and exit
  -e, --execute  actually execute the commands otherwise just print
  -v, --verbose  verbose output
```

### Checking Status

The first basic command `status` will show you the current differences between the repo and all the sources, here is an example for some ubuntu setup.

```
brice@server:config/server-ubuntu% cat .syncfiles
{"dirmap":{".":"~/","etc":"/etc","lib":"/lib","unifi":"/var/lib/unifi","bin":"~/bin"},"ignore":["README.md","doc","*~"]}
brice@server:config/server-ubuntu% syncfiles.py status
. .bashrc                                                 = ~/.bashrc
. etc/apt/sources.list                                    = /etc/apt/sources.list
. etc/network/interfaces                                  = /etc/network/interfaces
. etc/hosts                                               = /etc/hosts
. etc/apache2/apache2.conf                                = /etc/apache2/apache2.conf
. etc/fstab                                               = /etc/fstab
M etc/postfix/main.cf                                     < /etc/postfix/main.cf
. lib/systemd/system/snort.service                        = /lib/systemd/system/snort.service
. unifi/sites/default/config.gateway.json                 = /var/lib/unifi/sites/default/config.gateway.json
. bin/check_disk.sh                                       = ~/bin/check_disk.sh
```

* `.` means the files are the same
* `M` means the files are difference and the `<` or `>` indicates which direction should a sync copy the files (based on most recent timestamp)
* `?` means the files in the repo does not exist in the destination directory

You can also run `diff` or `difftool` that will show the diffs between the source and the repo

### synchronisation

The following command can be run to copy files between the repo and source:

* `install` will copy all the files from the repo to the source
* `sync` will synchronize all the files based on the most recent timestamp (will use the direction indicated by the `status` command).
* `push` will copy all the modified existing files from the repo to the source
* `pull` will copy all the modified existing fifes from the source to the repo

Each command by default will always only print what it would do, and in order to actually execute the copy you need to provide the `-e` or `--execute` flag


# simctl.py

This helps workflow around finding files in the simulator. It uses andcomplements `simctl` provided by apple. In addition, if you add a small snippet of code to your apps to save a small hidden file in the documents directory on startup, it will also help finding old simulator, which can be very handy when we need to move files from one version to the other, when Xcode upgrades for instance.

## Adding a needle to find the folders

Using the same idea as [Simulator Data Finder](https://github.com/roznet/iossimfinder), once you add a snippet of code that sames a file `.simneedle.{bundleIdentifier}`

`simctl.py` will then use that hidden file to find folder for simulator data and what app bundle they correspond to

## Finding simulator data folder

You can list the simulator available with `simctl.py list` which is a simple wrapper on simctl. The wrapper allows quick filter to find specific simulators for example `simctl.py -s 14.1 -name iphone list` 

## Find apps container in older simulators

The first functionality is the equivalent of get_app_container for older simulator that are no longer available. It finds the app using the needle mechanism.

The key functionality is the `upgrade` command, which will list for the matching simulator the apps it found that can be moved to the newest simulators, along with the command that will execute the upgrade.


# genstrings.py

This utility is a wrapper around the `genstrings` utility provided by `xcode` to generate `Localized.strings`. It help keep the resulting `Localized.strings` files organized and grouped by comment, merges the new translation on top of existing ones and adds basic ability to mark the translation needed to be reviewed for quick access

## Usage

```
usage: genstrings.py [-h] [-c] [-s] [-n NATIVE] [-r] [-v] Command [SRCDIR [SRCDIR ...]]

Check configuration

positional arguments:
  Command               command to execute:
                          build: Rebuild database
                          difftool: Rebuild and diff changes
  SRCDIR                Directory where to search for source files

optional arguments:
  -h, --help            show this help message and exit
  -c, --clear           clear existing attributes
  -s, --save            save output otherwise just print
  -n NATIVE, --native NATIVE
                        native language, will mark translation for that language
  -r, --remove          remove deleted entries
  -v, --verbose         verbose output
```


