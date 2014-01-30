#!/usr/bin/env python2
# Copyright 2014 The Rust Project Developers. See LICENSE for more details.
"""cargo-lite, a dirt simple Rust package manager

Usage:
  cargo-lite.py install [--git | --hg | --local] [<path>] [<package>]
  cargo-lite.py build [<path>]
  cargo-lite.py --version

Options:
  -h --help     Show this screen.
  --git         Fetch source using git (inferred if <package> ends in .git)
  --hg          Fetch source using hg (never inferred)
  --version     Show version.

"""

import sys
import os
import shutil

from docopt import docopt
try:
    from sh import git
except ImportError:
    def git(*args, **kwargs):
        sys.stderr.write("git not installed, but asked for!\n")
        sys.exit(1)
try:
    from sh import hg
except ImportError:
    def hg(*args, **kwargs):
        sys.stderr.write("hg not installed, but asked for!\n")
        sys.exit(1)
try:
    from sh import rustc
except ImportError:
    sys.stderr.write("cargo-lite.py requires rustc to be installed\n")
    sys.exit(1)

import sh
import toml

VERSION = 'cargo-lite.py 0.1.0'


def expand(path):
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def repodir():
    return expand("~/.rust")


def libdir():
    dr = expand(os.path.join(repodir(), "lib"))
    if not os.path.exists(dr):
        os.makedirs(dr)
    return dr


def from_pkgdir(path):
    path = expand(os.path.join(path, "cargo-lite.conf"))
    if not os.path.exists(path):
        raise Exception("{} does not exist".format(path))
    return toml.loads(open(path).read())


class cd:
    """Context manager for changing the current working directory, creating if necessary"""
    def __init__(self, newPath):
        newPath = os.path.abspath(os.path.expandvars(os.path.expanduser(newPath)))
        self.newPath = newPath
        if not os.path.exists(newPath):
            os.makedirs(newPath)

    def __enter__(self):
        self.savedPath = os.getcwd()
        os.chdir(self.newPath)

    def __exit__(self, etype, value, traceback):
        os.chdir(self.savedPath)


def success(output):
    if output.exit_code != 0:
        sys.stderr.write("command failed: {}".format(str(output)))
        sys.exit(1)


def fetch(args):
    "Fetch a package's source, returning the path to it"

    path = args['<path>']
    pkg = args['<package>']

    if path is None:
        dest = os.path.join(repodir(), os.path.split(expand("."))[-1])
        if os.path.exists(dest):
            print "Already found fetched copy of cwd, skipping"
            return dest
        shutil.copytree(expand("."), dest)
        return dest

    local = args['--local']
    use_git = args['--git']
    use_hg = args['--hg']

    pkg = args['<package>']
    if pkg is None:
        pkg, ext = os.path.splitext(os.path.basename(path))


    if not use_hg and not use_git and not local:
        if path.endswith('.git'):
            use_git = True
        else:
            sys.stderr.write("error: neither --git nor --hg given, and can't infer from package path\n")
            os.exit(1)

    dest = os.path.join(expand(repodir()), pkg)
    if os.path.exists(dest):
        print "Already found fetched copy of {} at {}, skipping".format(pkg,dest)
        return dest

    if local:
        shutil.copytree(expand(path), dest)
    elif git:
        git.clone(path, dest)
    elif hg:
        hg.clone(path, dest)
    return dest


def build(args, conf):
    if 'subpackages' in conf:
        s = conf['subpackages']
        for subpackage in s:
            with cd(subpackage):
                build(args, from_pkgdir("."))

    if 'build' in conf:
        b = conf['build']
        if 'crate_root' in b:
            crate_root = os.path.abspath(b['crate_root'])
            output = rustc("--crate-file-name", "--rlib", "--staticlib", "--dylib", crate_root, _iter=True)
            if output.exit_code != 0:
                sys.stderr.write("--crate-file-name failed, status {}, stderr:\n".format(output.exit_code))
                sys.stderr.write(str(output))
                sys.exit(1)

            names = list(output)

            if all([os.path.exists(os.path.join(libdir(), x)) for x in names if x != ""]):
                print "all artifacts present, not rebuilding (TODO: add way to rebuild)"
                return

            args = b.get('rustc_args', [])
            args.append(crate_root)
            args.append("--rlib")
            args.append("--staticlib")
            args.append("--dylib")
            args.append("-L")
            args.append(libdir())
            output = rustc(*args)

            if output.exit_code != 0:
                sys.stderr.write("building {} with rustc failed with status {}, output:\n".format(crate_root, output.exit_code))
                sys.stderr.write(str(output))
                sys.exit(1)
            for fname in map(lambda x: x.strip(), filter(lambda x: x != '', names)):
                shutil.copy(os.path.join(os.path.dirname(crate_root), fname), libdir())

        elif 'build_cmd' in b:
            try:
                out = sh.Command(b["build_cmd"])()
            except sh.ErrorReturnCode as e:
                print "The build command for {} failed with exit code {}".format(
                        args['<path>'], e.exit_code)
                print e.message
                sys.exit(1)
            if not out.startswith("cargo-lite: "):
                raise Exception("malformed output in build_cmd's stdout")
            if out.startswith("cargo-lite: artifacts"):
                for artifact in filter(lambda x: x != "", out.split("\n")[1:]):
                    shutil.copy(artifact, libdir())
            elif out.startswith("carg-lite: crate_root="):
                args = dict(args)
                del args["build"]["build_cmd"]
                args["build"]["crate_root"] = out.replace("cargo-lite: crate_root=", "")
                install(args)
            else:
                sys.stderr.write(str(out))
                sys.stderr.write("unrecognized directive in build_cmd output\n")
                sys.exit(1)
        else:
            raise Exception("unrecognized build information in cargo-lite.conf")
    elif not 'subpackages' in conf:
        raise Exception("no build information in cargo-lite.conf!")


def install_deps(args, conf):
    for dep in conf.get('deps', []):
        # whee prepend!
        dep.insert(0, 'install')
        install(docopt(__doc__, version=VERSION, argv=dep))


def install(args):
    path = fetch(args)
    conf = from_pkgdir(path)
    install_deps(args, conf)

    with cd(path):
        build(args, conf)


def buildcmd(args):
    p = args["<path>"]
    if p is None:
        p = os.getcwd()
    conf = from_pkgdir(p)
    install_deps(args, conf)
    b = conf["build"]
    if "crate_root" in b:
        args = b.get("rustc_args", [])
        args.append(b["crate_root"])
        args.append("-L")
        args.append(libdir())
        args.append("--rlib")
        args.append("--staticlib")
        args.append("--dylib")
        success(rustc(*args))
    elif "build_cmd" in b:
        # TODO: pass it libdir somehow, perhaps in env var?
        success(sh.Command(b["build_cmd"])())
    else:
        sys.stderr.write("unrecognized build information in cargo-lite.conf")
        sys.exit(1)


if __name__ == '__main__':
    arguments = docopt(__doc__, version=VERSION)
    if arguments['install']:
        install(arguments)
    elif arguments['build']:
        buildcmd(arguments)
    else:
        sys.stderr.write("unsupported command or command NYI")
        sys.exit(1)
