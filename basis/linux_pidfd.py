"""Linux process-descriptor signalling, including Python builds without pidfds.

Prefer Python's bindings, then libc's typed wrappers. On 64-bit x86_64/aarch64
only, libc.syscall provides the Linux ABI fallback (pidfd_open 434 and
pidfd_send_signal 424). Missing kernel support fails closed. There is deliberately
no numeric-PID os.kill fallback.
"""
from __future__ import annotations

import ctypes
import errno
from functools import lru_cache
import os
import platform
import signal
import sys

_PYTHON_OPEN = getattr(os, "pidfd_open", None)
_PYTHON_SEND = getattr(signal, "pidfd_send_signal", None)
_SYSCALLS = {"pidfd_open": 434, "pidfd_send_signal": 424}


@lru_cache(maxsize=1)
def _libc():
    return ctypes.CDLL(None, use_errno=True)


def _linux():
    if sys.platform != "linux":
        raise RuntimeError("Safe process-descriptor signalling requires Linux")


def _integer(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(name + " must be an integer")
    if value < (1 if positive else 0) or value > 2**31 - 1:
        raise ValueError(name + " is outside the supported integer range")
    return value


def _checked(result, operation):
    if result < 0:
        error = ctypes.get_errno() or errno.EIO
        raise OSError(error, operation + ": " + os.strerror(error))
    return result


def _fallback(operation, args, types):
    library = _libc()
    function = getattr(library, operation, None)
    ctypes.set_errno(0)
    if function is not None:
        function.argtypes = types
        function.restype = ctypes.c_int
        return _checked(function(*args), operation)
    # These are Linux x86-64 and asm-generic AArch64 syscall numbers. Do not
    # guess numbers for another architecture or the x32/32-bit ABI.
    if platform.machine().lower() not in {"x86_64", "aarch64"} or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("No libc " + operation + " wrapper; syscall fallback supports only 64-bit x86_64/aarch64")
    syscall = getattr(library, "syscall", None)
    if syscall is None:
        raise RuntimeError("libc does not expose process-descriptor syscalls")
    syscall.argtypes = None
    syscall.restype = ctypes.c_long
    converted = [ctypes.c_void_p(None) if value is None else ctypes.c_long(value) for value in args]
    return _checked(syscall(ctypes.c_long(_SYSCALLS[operation]), *converted), operation)


def pidfd_open(pid):
    _linux()
    pid = _integer(pid, "pid", positive=True)
    if callable(_PYTHON_OPEN):
        return _PYTHON_OPEN(pid, 0)
    return _fallback("pidfd_open", (pid, 0), [ctypes.c_int, ctypes.c_uint])


def pidfd_send_signal(fd, sig):
    _linux()
    fd, sig = _integer(fd, "fd"), _integer(sig, "signal")
    if callable(_PYTHON_SEND):
        _PYTHON_SEND(fd, sig, None, 0)
    else:
        _fallback("pidfd_send_signal", (fd, sig, None, 0),
                  [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint])


def probe_pidfd_support():
    """Check both operations on ourselves with signal 0 before target signals.

    Signal zero only tests accessibility; it delivers no signal. This detects
    unsupported WSL/Linux kernels and security-policy errors without signalling
    any target job. Open descriptors are closed even if probing fails.
    """
    fd = None
    try:
        fd = pidfd_open(os.getpid())
        pidfd_send_signal(fd, 0)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError("Linux pidfd capability check failed; no target signals sent: " + str(exc)) from exc
    finally:
        if fd is not None:
            os.close(fd)
