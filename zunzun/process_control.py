"""Helpers for the processes the site forks: long-running fits and home page housekeeping.

Fits are forked from gunicorn worker processes, so without care they inherit gunicorn's
signal handlers. gunicorn's SIGTERM handler only sets a flag, so SIGTERM (which is what
multiprocessing's terminate() sends) did not stop a fit or its pool workers: an abandoned or
hung fit kept a core busy until it finished on its own, however long that took.
"""
import logging
import os
import signal

import psutil

import settings

_logger = logging.getLogger(__name__)

# signals gunicorn's worker installs handlers for (gunicorn/workers/base.py, init_signals)
_INHERITED_SIGNALS = ('SIGTERM', 'SIGINT', 'SIGQUIT', 'SIGHUP', 'SIGWINCH', 'SIGUSR1', 'SIGUSR2', 'SIGABRT')


def reset_inherited_signal_handlers():
    """Call first thing in a forked child: restore default signal behaviour so SIGTERM ends it."""
    for name in _INHERITED_SIGNALS:
        try:
            signal.signal(getattr(signal, name), signal.SIG_DFL)
        except (AttributeError, OSError, ValueError):
            pass
    try:
        signal.set_wakeup_fd(-1) # gunicorn's wakeup pipe belongs to the worker, not to us
    except (AttributeError, ValueError):
        pass


def pid_is_running(pid):
    """True if pid is a live process. Zombies count as finished: a fit that exited stays a
    zombie until its gunicorn worker reaps it."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def client_ip(request):
    """The browser's address. nginx proxies from localhost and overwrites X-Forwarded-For
    with the real client address (proxy_set_header X-Forwarded-For $remote_addr), so the
    header is trusted only when the request came from localhost."""
    remote = request.META.get('REMOTE_ADDR', '')
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if remote in ('127.0.0.1', '::1') and forwarded:
        return forwarded.split(',')[-1].strip()
    return remote


# Registry of running fits: one file per fit process, named by pid, holding the client IP.
# It lives beside the session database rather than in temp/, whose cleanup deletes files.
def _registry_dir():
    path = os.path.join(os.path.dirname(settings.DATABASES['default']['NAME']), 'active_fits')
    os.makedirs(path, exist_ok=True)
    return path


def register_fit(pid, ip):
    try:
        with open(os.path.join(_registry_dir(), str(int(pid))), 'w') as f:
            f.write(ip)
    except OSError:
        _logger.warning('could not register fit process %s', pid, exc_info=True)


def unregister_fit(pid):
    try:
        os.remove(os.path.join(_registry_dir(), str(int(pid))))
    except OSError:
        pass


def count_active_fits(ip):
    """Running fits started from ip. Entries for processes that have gone are removed."""
    count = 0
    try:
        directory = _registry_dir()
        names = os.listdir(directory)
    except OSError:
        _logger.warning('could not read the active fit registry', exc_info=True)
        return 0 # fail open: a registry problem must not stop all fitting
    for name in names:
        path = os.path.join(directory, name)
        if not name.isdigit():
            continue
        if not pid_is_running(name):
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        try:
            with open(path) as f:
                if f.read().strip() == ip:
                    count += 1
        except OSError:
            pass
    return count
