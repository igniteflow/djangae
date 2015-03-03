# None of this is django-specific. Don't import from django.

import os
import sys
import contextlib
import subprocess
import getpass
import logging
import djangae.utils as utils

_SCRIPT_NAME = 'dev_appserver.py'

_API_SERVER = None


class Filter(object):
    def filter(self, record):
        if record.funcName == '__StarSchemaQueryPlan' and record.module == 'datastore_sqlite_stub':
            return 0
        elif record.funcName == 'Run' and record.module == 'datastore':
            return 0
        else:
            return 1


def _disable_sqlite_stub_logging():
    """
        For some reason, Google decided to log all queries at debug level to the
        root logger when running stuff locally. This switches that off (if you want it, then just
        remove the filter)
    """
    logging.getLogger().addFilter(Filter())


def _find_sdk_from_python_path():
    import google.appengine
    # Make sure we get the path of the 'google' module which contains 'appengine', as it's possible
    # that there are several.
    return os.path.abspath(os.path.dirname(os.path.dirname(google.appengine.__path__[0])))


def _find_sdk_from_path():
    # Assumes `script_name` is on your PATH - SDK installers set this up
    which = 'where' if sys.platform == "win32" else 'which'
    path = subprocess.check_output([which, _SCRIPT_NAME]).strip()
    sdk_dir = os.path.dirname(os.path.realpath(path))

    if os.path.exists(os.path.join(sdk_dir, 'bootstrapping')):
        # Cloud SDK
        sdk_dir = os.path.abspath(os.path.join(sdk_dir, '..', 'platform', 'google_appengine'))
        if not os.path.exists(sdk_dir):
            raise RuntimeError(
                'The Cloud SDK is on the path, but the app engine SDK dir could not be found'
            )
        else:
            return sdk_dir
    else:
        # Regular App Engine SDK
        return sdk_dir


def _create_dispatcher(configuration, options):
    from google.appengine.tools.devappserver2 import dispatcher
    from google.appengine.tools.devappserver2.devappserver2 import (
        DevelopmentServer, _LOG_LEVEL_TO_RUNTIME_CONSTANT
    )

    if hasattr(_create_dispatcher, "singleton"):
        return _create_dispatcher.singleton

    _create_dispatcher.singleton = dispatcher.Dispatcher(
        configuration,
        options.host,
        options.port,
        options.auth_domain,
        _LOG_LEVEL_TO_RUNTIME_CONSTANT[options.log_level],
        DevelopmentServer._create_php_config(options),
        DevelopmentServer._create_python_config(options),
        DevelopmentServer._create_java_config(options),
        DevelopmentServer._create_cloud_sql_config(options),
        DevelopmentServer._create_vm_config(options),
        DevelopmentServer._create_module_to_setting(options.max_module_instances,
                                       configuration, '--max_module_instances'),
        options.use_mtime_file_watcher,
        options.automatic_restart,
        options.allow_skipped_files,
        DevelopmentServer._create_module_to_setting(options.threadsafe_override,
                                       configuration, '--threadsafe_override')
    )

    return _create_dispatcher.singleton

@contextlib.contextmanager
def _local(devappserver2=None, configuration=None, options=None, wsgi_request_info=None, **kwargs):
    global _API_SERVER

    _disable_sqlite_stub_logging()

    original_environ = os.environ.copy()

    # Silence warnings about this being unset, localhost:8080 is the dev_appserver default
    os.environ.setdefault("HTTP_HOST", "localhost:8080")
    os.environ['SERVER_NAME'] = os.environ['HTTP_HOST'].split(':', 1)[0]
    os.environ['DEFAULT_VERSION_HOSTNAME'] = '%s:%s' % (os.environ['SERVER_NAME'], 8000)

    devappserver2._setup_environ(configuration.app_id)
    storage_path = devappserver2._get_storage_path(options.storage_path, configuration.app_id)

    dispatcher = _create_dispatcher(configuration, options)
    request_data = wsgi_request_info.WSGIRequestInfo(dispatcher)
    # Remember the wsgi request info object so it can be reused to avoid duplication.
    dispatcher._request_data = request_data

    _API_SERVER = devappserver2.DevelopmentServer._create_api_server(
        request_data, storage_path, options, configuration)

    try:
        yield
    finally:
        os.environ = original_environ


def get_auth_func():
    logging.basicConfig(format='%(message)s')
    logger = logging.getLogger(__name__)

    GOOGLE_AUTH = os.environ.get('GOOGLE_AUTH')
    if GOOGLE_AUTH:
        try:
            email, password = GOOGLE_AUTH.split(':')
            logger.info(
                'Using authentication credentials from environment variable GOOGLE_AUTH for account: {}'.format(email)
            )
            auth_func = lambda: (email, password)
        except ValueError:
            raise RuntimeError('$GOOGLE_AUTH must be of format `foo@example.com:password`')
    else:
        auth_func = lambda: (raw_input('Google Account Login: '), getpass.getpass('Password: '))
    return auth_func


@contextlib.contextmanager
def _remote(configuration=None, remote_api_stub=None, apiproxy_stub_map=None, **kwargs):

    original_apiproxy = apiproxy_stub_map.apiproxy

    if configuration.app_id.startswith('dev~'):
        app_id = configuration.app_id[4:]
    else:
        app_id = configuration.app_id

    os.environ['HTTP_HOST'] = '{0}.appspot.com'.format(app_id)
    os.environ['DEFAULT_VERSION_HOSTNAME'] = os.environ['HTTP_HOST']

    remote_api_stub.ConfigureRemoteApi(
        None,
        '/_ah/remote_api',
        get_auth_func(),
        servername='{0}.appspot.com'.format(app_id),
        secure=True,
    )

    ps1 = getattr(sys, 'ps1', None)
    red = "\033[0;31m"
    native = "\033[m"
    sys.ps1 = red + '(remote) ' + app_id + native + ' >>> '

    try:
        yield
    finally:
        apiproxy_stub_map.apiproxy = original_apiproxy
        sys.ps1 = ps1


@contextlib.contextmanager
def _test(**kwargs):
    yield

LOCAL = 'local'
REMOTE = 'remote'
TEST = 'test'
SANDBOXES = {
    LOCAL: _local,
    REMOTE: _remote,
    TEST: _test,
}

_OPTIONS = None

@contextlib.contextmanager
def activate(sandbox_name, add_sdk_to_path=False, **overrides):
    """Context manager for command-line scripts started outside of dev_appserver.

    :param sandbox_name: str, one of 'local', 'remote' or 'test'
    :param add_sdk_to_path: bool, optionally adds the App Engine SDK to sys.path
    :param options_override: an options structure to pass down to dev_appserver setup

    Available sandboxes:

      local: Adds libraries specified in app.yaml to the path and initializes local service stubs as though
             dev_appserver were running.

      remote: Adds libraries specified in app.yaml to the path and initializes remote service stubs.

      test: Adds libraries specified in app.yaml to the path and sets up no service stubs. Use this
            with `google.appengine.ext.testbed` to provide isolation for tests.

    Example usage:

        import djangae.sandbox as sandbox

        with sandbox.activate('local'):
            from django.core.management import execute_from_command_line
            execute_from_command_line(sys.argv)

    """
    if sandbox_name not in SANDBOXES:
        raise RuntimeError('Unknown sandbox "{}"'.format(sandbox_name))

    project_root = utils.find_project_root()

    # Setup paths as though we were running dev_appserver. This is similar to
    # what the App Engine script wrappers do.

    if add_sdk_to_path:
        try:
            import wrapper_util  # Already on sys.path
        except ImportError:
            sys.path[0:0] = [_find_sdk_from_path()]
            import wrapper_util
    else:
        try:
            import wrapper_util
        except ImportError:
            raise RuntimeError("Couldn't find a recent enough Google App Engine SDK, make sure you are using at least 1.9.6")

    original_path = sys.path[:]

    sdk_path = _find_sdk_from_python_path()
    _PATHS = wrapper_util.Paths(sdk_path)
    sys.path = (_PATHS.script_paths(_SCRIPT_NAME) + _PATHS.scrub_path(_SCRIPT_NAME, sys.path))

    # Gotta set the runtime properly otherwise it changes appengine imports, like wepapp
    # when you are not running dev_appserver
    import yaml
    with open(os.path.join(project_root, 'app.yaml'), 'r') as app_yaml:
        app_yaml = yaml.load(app_yaml)
        os.environ['APPENGINE_RUNTIME'] = app_yaml.get('runtime', '')


    # Initialize as though `dev_appserver.py` is about to run our app, using all the
    # configuration provided in app.yaml.
    import google.appengine.tools.devappserver2.application_configuration as application_configuration
    import google.appengine.tools.devappserver2.python.sandbox as sandbox
    import google.appengine.tools.devappserver2.devappserver2 as devappserver2
    import google.appengine.tools.devappserver2.wsgi_request_info as wsgi_request_info
    import google.appengine.ext.remote_api.remote_api_stub as remote_api_stub
    import google.appengine.api.apiproxy_stub_map as apiproxy_stub_map

    # The argparser is the easiest way to get the default options.
    options = devappserver2.PARSER.parse_args([project_root])
    options.enable_task_running = False # Disable task running by default, it won't work without a running server

    for option in overrides:
        if not hasattr(options, option):
            raise ValueError("Unrecognized sandbox option: {}".format(option))

        setattr(options, option, overrides[option])

    configuration = application_configuration.ApplicationConfiguration(options.config_paths)

    # Take dev_appserver paths off sys.path - GAE apps cannot access these
    sys.path = original_path[:]
    # Enable built-in libraries from app.yaml without enabling the full sandbox.
    module = configuration.modules[0]
    for l in sandbox._enable_libraries(module.normalized_libraries):
        sys.path.insert(0, l)

    try:
        global _OPTIONS
        _OPTIONS = options # Store the options globally so they can be accessed later
        kwargs = dict(
            devappserver2=devappserver2,
            configuration=configuration,
            options=options,
            wsgi_request_info=wsgi_request_info,
            remote_api_stub=remote_api_stub,
            apiproxy_stub_map=apiproxy_stub_map,
        )
        with SANDBOXES[sandbox_name](**kwargs):
            yield

    finally:
        sys.path = original_path

@contextlib.contextmanager
def allow_mode_write():
    from google.appengine.tools.devappserver2.python import stubs

    original_modes = stubs.FakeFile.ALLOWED_MODES
    new_modes = set(stubs.FakeFile.ALLOWED_MODES)
    new_modes.add('w')
    new_modes.add('wb')
    stubs.FakeFile.ALLOWED_MODES = frozenset(new_modes)
    try:
        yield
    finally:
        stubs.FakeFile.ALLOWED_MODES = original_modes


def allow_modules(func, *args):
    """
        dev_appserver likes to kill your imports with meta_path madness so you
        use the internal ones instead of system ones, this wrapper reloads the
        modules and patches the google internal ones with the __dict__ from the
        system modules, this seems to be the cleanest way to do this even though
        it is a bit hacky
    """
    def _wrapped(*args, **kwargs):
        import sys

        import subprocess
        import os
        import tempfile
        import select
        # Clear the meta_path so google does not screw our imports, make a copy
        # of the old one
        old_meta_path = sys.meta_path
        sys.meta_path = []
        patch_modules = [os, tempfile, select, subprocess]

        import copy
        environ = copy.copy(os.environ)

        for mod in patch_modules:
            _system = reload(mod)
            mod.__dict__.update(_system.__dict__)

        # We have to maintain the environment, or bad things happen
        os.environ = environ

        try:
            return func(*args, **kwargs)
        finally:
            # Restore the original path
            sys.meta_path = old_meta_path
            # Reload the original modules
            for mod in patch_modules:
                _system = reload(mod)
                mod.__dict__.update(_system.__dict__)

    return _wrapped
