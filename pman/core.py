from __future__ import print_function

import imp
import fnmatch
import os
import shutil
import subprocess
import time
from collections import OrderedDict
try:
    import configparser
except ImportError:
    import ConfigParser as configparser

import pkg_resources

from . import toml


if 'FileNotFoundError' not in globals():
    #pylint:disable=redefined-builtin
    FileNotFoundError = IOError


class PManException(Exception):
    pass


class NoConfigError(PManException):
    pass


class CouldNotFindPythonError(PManException):
    pass


class BuildError(PManException):
    pass


class FrozenEnvironmentError(PManException):
    def __init__(self):
        PManException.__init__(self, "Operation not supported in frozen applications")


_CONFIG_DEFAULTS = OrderedDict([
    ('general', OrderedDict([
        ('name', 'Game'),
        ('renderer', 'basic'),
        ('material_mode', 'legacy'),
    ])),
    ('build', OrderedDict([
        ('asset_dir', 'assets/'),
        ('export_dir', 'game/assets/'),
        ('ignore_patterns', ['*.blend1', '*.blend2']),
        ('converters', ['blend2bam']),
    ])),
    ('run', OrderedDict([
        ('main_file', 'game/main.py'),
        ('auto_build', True),
        ('auto_save', True),
    ])),
])

_USER_CONFIG_DEFAULTS = OrderedDict([
    ('blender', OrderedDict([
        ('last_path', 'blender'),
        ('use_last_path', True),
    ])),
    ('python', OrderedDict([
        ('path', ''),
    ])),
])


def _convert_conf_to_toml(configpath):
    config = configparser.ConfigParser()
    config.read(configpath)

    confdict = {
        s: dict(config.items(s))
        for s in config.sections()
    }

    if 'build' in confdict:
        confdict['build']['ignore_patterns'] = [
            i.strip() for i in confdict['build']['ignore_patterns'].split(',')
        ]
    if 'run' in confdict:
        confdict['run']['auto_build'] = config.getboolean('run', 'auto_build')
        confdict['run']['auto_save'] = config.getboolean('run', 'auto_save')
    if 'blender' in confdict:
        confdict['blender']['use_last_path'] = config.getboolean('blender', 'use_last_path')
    return confdict


def _update_conf(config):
    if 'general' in config:
        if 'render_plugin' in config['general']:
            if '/' in config['general']['render_plugin']:
                # Convert from path to module
                renderplugin = config['general']['render_plugin']
                rppath = get_abs_path(config, renderplugin)
                maindir = os.path.dirname(get_abs_path(config, config['run']['main_file']))
                rppath = os.path.splitext(os.path.relpath(rppath, maindir))[0]
                module_parts = rppath.split(os.sep)
                modname = '.'.join(module_parts)
                config['general']['render_plugin'] = modname
            config['general']['renderer'] = config['general']['render_plugin']
            del config['general']['render_plugin']
        if 'converter_hooks' in config['general']:
            config['general']['converters'] = [
                'blend2bam' if i == 'pman.hooks.converter_blend_bam' else i
                for i in config['general']['converter_hooks']
            ]
            del config['general']['convert_hooks']


def _get_config(startdir, conf_name, defaults):
    try:
        if startdir is None:
            startdir = os.getcwd()
    except FileNotFoundError:
        # The project folder was deleted on us
        raise NoConfigError("Could not find config file")

    dirs = os.path.abspath(startdir).split(os.sep)

    while dirs:
        cdir = os.sep.join(dirs)
        if cdir.strip() and conf_name in os.listdir(cdir):
            configpath = os.path.join(cdir, conf_name)

            with open(configpath) as f:
                confdata = f.read()
                istoml = (
                    '"' in confdata or
                    "'" in confdata or
                    confdata == ''
                )

            if istoml:
                confdict = toml.load(configpath)
            else:
                confdict = _convert_conf_to_toml(configpath)
            confdict = {
                k: dict(defaults.get(k, {}), **confdict.get(k, {}))
                for k in set(defaults.keys()) | set(confdict.keys())
            }

            confdict['internal'] = {
                'projectdir': os.path.dirname(configpath),
            }

            _update_conf(confdict)

            return confdict

        dirs.pop()

    # No config found
    raise NoConfigError("Could not find config file")


def get_config(startdir=None):
    return _get_config(startdir, '.pman', _CONFIG_DEFAULTS)


def config_exists(startdir=None):
    try:
        get_config(startdir)
        have_config = True
    except NoConfigError:
        have_config = False

    return have_config


def get_user_config(startdir=None):
    try:
        return _get_config(startdir, '.pman.user', _USER_CONFIG_DEFAULTS)
    except NoConfigError:
        # No user config, just create one
        config = get_config(startdir)
        file_path = os.path.join(config['internal']['projectdir'], '.pman.user')
        print("Creating user config at {}".format(file_path))
        open(file_path, 'w').close()

        return _get_config(startdir, '.pman.user', _USER_CONFIG_DEFAULTS)


def _write_config(config, conf_name):
    writecfg = config.copy()
    del writecfg['internal']

    with open(os.path.join(config['internal']['projectdir'], conf_name), 'w') as f:
        toml.dump(writecfg, f)


def write_config(config):
    _write_config(config, '.pman')


def write_user_config(user_config):
    _write_config(user_config, '.pman.user')


def is_frozen():
    return imp.is_frozen(__name__)


def create_project(projectdir='.'):
    if is_frozen():
        raise FrozenEnvironmentError()

    if not os.path.exists(projectdir):
        os.makedirs(projectdir)

    confpath = os.path.join(projectdir, '.pman')
    if os.path.exists(confpath):
        print("Updating project in {}".format(projectdir))
    else:
        print("Creating new project in {}".format(projectdir))

        # Touch config file to make sure it is present
        with open(confpath, 'a') as _:
            pass

    config = get_config(projectdir)
    write_config(config)

    pmandir = os.path.dirname(__file__)
    templatedir = os.path.join(pmandir, 'templates')

    print("Creating directories...")

    dirs = [
        config['build']['asset_dir'],
        'game',
        'tests',
    ]

    dirs = [os.path.join(projectdir, i) for i in dirs]

    for d in dirs:
        if os.path.exists(d):
            print("\tSkipping existing directory: {}".format(d))
        else:
            print("\tCreating directory: {}".format(d))
            os.mkdir(d)

    templatefiles = (
        ('main.py', config['run']['main_file']),
        ('requirements.txt', 'requirements.txt'),
        ('setup.py', 'setup.py'),
        ('setup.cfg', 'setup.cfg'),
        ('pylintrc', '.pylintrc'),
        ('test_imports.py', 'tests/test_imports.py'),
    )

    for tmplfile in templatefiles:
        src = os.path.join(templatedir, tmplfile[0])
        dst = os.path.join(projectdir, tmplfile[1])
        print("Creating {}".format(dst))
        if os.path.exists(dst):
            print("\t{} already exists, skipping".format(dst))
        else:
            shutil.copyfile(src, dst)
            print("\t{} copied to {}".format(src, dst))


def get_abs_path(config, path):
    return PMan(config=config).get_abs_path(path)


def get_rel_path(config, path):
    return PMan(config=config).get_rel_path(path)

def get_python_program(config=None):
    python_programs = [
        'ppython',
        'python3',
        'python',
        'python2',
    ]

    if config is not None:
        user_config = get_user_config(config['internal']['projectdir'])
        confpy = user_config['python']['path']
        if confpy:
            python_programs.insert(0, confpy)

    # Check to see if there is a version of Python that can import panda3d
    for pyprog in python_programs:
        args = [
            pyprog,
            '-c',
            'import panda3d.core; import direct',
        ]
        with open(os.devnull, 'w') as f:
            try:
                retcode = subprocess.call(args, stderr=f)
            except FileNotFoundError:
                retcode = 1

        if retcode == 0:
            return pyprog

    # We couldn't find a python program to run
    raise CouldNotFindPythonError('Could not find a Python version with Panda3D installed')


def build(config=None):
    PMan(config=config).build()


def run(config=None):
    PMan(config=config).run()


def dist(config=None, build_installers=True, platforms=None):
    PMan(config=config).dist(build_installers, platforms)


def create_renderer(base, config=None):
    if config is None:
        try:
            config = get_config()
        except NoConfigError:
            print("Could not find pman config, falling back to basic renderer")
            config = None

    renderername = config['general']['renderer'] if config else 'basic'

    if not renderername:
        renderername = _CONFIG_DEFAULTS['general']['renderer']

    for entry_point in pkg_resources.iter_entry_points('pman.renderers'):
        if entry_point.name == renderername:
            renderer = entry_point.load()
            break
    else:
        from .basicrenderer import BasicRenderer
        print("Could not find renderer for {0}, falling back to basic renderer".format(renderername))
        renderer = BasicRenderer

    return renderer(base)


def converter_copy(_config, _user_config, srcdir, dstdir, assets):
    for asset in assets:
        src = asset
        dst = src.replace(srcdir, dstdir)
        print('Copying non-blend file from "{}" to "{}"'.format(src, dst))
        if not os.path.exists(os.path.dirname(dst)):
            os.makedirs(os.path.dirname(dst))
        shutil.copyfile(src, dst)


class PMan(object):
    def __init__(self, config=None, config_startdir=None):
        if config:
            self.config = config
            self.user_config = get_user_config(config['internal']['projectdir'])
        else:
            self.config = get_config(config_startdir)
            self.user_config = get_user_config(config_startdir)

        if is_frozen():
            self.converters = []
        else:
            self.converters = [
                entry_point.load()
                for entry_point in pkg_resources.iter_entry_points('pman.converters')
                if entry_point.name in self.config['build']['converters']
            ]

    def get_abs_path(self, path):
        return os.path.join(
            self.config['internal']['projectdir'],
            path
        )

    def get_rel_path(self, path):
        return os.path.relpath(path, self.config['internal']['projectdir'])

    def build(self):
        if is_frozen():
            raise FrozenEnvironmentError()

        if hasattr(time, 'perf_counter'):
            #pylint:disable=no-member
            stime = time.perf_counter()
        else:
            stime = time.time()
        print("Starting build")

        srcdir = self.get_abs_path(self.config['build']['asset_dir'])
        dstdir = self.get_abs_path(self.config['build']['export_dir'])

        if not os.path.exists(srcdir):
            raise BuildError("Could not find asset directory: {}".format(srcdir))

        if not os.path.exists(dstdir):
            print("Creating asset export directory at {}".format(dstdir))
            os.makedirs(dstdir)

        print("Read assets from: {}".format(srcdir))
        print("Export them to: {}".format(dstdir))

        ignore_patterns = self.config['build']['ignore_patterns']
        print("Ignoring file patterns: {}".format(ignore_patterns))

        # Gather files and group by extension
        ext_asset_map = {}
        ext_dst_map = {}
        ext_converter_map = {}
        for converter in self.converters:
            ext_dst_map.update(converter.ext_dst_map)
            for ext in converter.supported_exts:
                ext_converter_map[ext] = converter

        for root, _dirs, files in os.walk(srcdir):
            for asset in files:
                src = os.path.join(root, asset)
                dst = src.replace(srcdir, dstdir)

                ignore_pattern = None
                for pattern in ignore_patterns:
                    if fnmatch.fnmatch(asset, pattern):
                        ignore_pattern = pattern
                        break
                if ignore_pattern is not None:
                    print('Skip building file {} that matched ignore pattern {}'.format(asset, ignore_pattern))
                    continue

                ext = os.path.splitext(asset)[1]

                if ext in ext_dst_map:
                    dst = dst.replace(ext, ext_dst_map[ext])

                if os.path.exists(dst) and os.stat(src).st_mtime <= os.stat(dst).st_mtime:
                    print('Skip building up-to-date file: {}'.format(dst))
                    continue

                if ext not in ext_asset_map:
                    ext_asset_map[ext] = []

                print('Adding {} to conversion list to satisfy {}'.format(src, dst))
                ext_asset_map[ext].append(os.path.join(root, asset))

        # Find which extensions have hooks available
        convert_hooks = []
        for ext, converter in ext_converter_map.items():
            if ext in ext_asset_map:
                convert_hooks.append((converter, ext_asset_map[ext]))
                del ext_asset_map[ext]

        # Copy what is left
        for ext in ext_asset_map:
            converter_copy(self.config, self.user_config, srcdir, dstdir, ext_asset_map[ext])

        # Now run hooks that non-converted assets are in place (copied)
        for convert_hook in convert_hooks:
            convert_hook[0](self.config, self.user_config, srcdir, dstdir, convert_hook[1])


        if hasattr(time, 'perf_counter'):
            #pylint:disable=no-member
            etime = time.perf_counter()
        else:
            etime = time.time()
        print("Build took {:.4f}s".format(etime - stime))

    def run(self):
        if is_frozen():
            raise FrozenEnvironmentError()

        mainfile = self.get_abs_path(self.config['run']['main_file'])
        print("Running main file: {}".format(mainfile))
        args = [get_python_program(self.config), mainfile]
        #print("Args: {}".format(args))
        subprocess.call(args, cwd=self.config['internal']['projectdir'])

    def dist(self, build_installers=True, platforms=None):
        if is_frozen():
            raise FrozenEnvironmentError()

        args = [
            get_python_program(self.config),
            'setup.py',
        ]

        if build_installers:
            args += ['bdist_apps']
        else:
            args += ['build_apps']

        if platforms is not None:
            args += ['-p', '{}'.format(','.join(platforms))]

        subprocess.call(args, cwd=self.config['internal']['projectdir'])
