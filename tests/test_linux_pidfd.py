"""Portable pidfd backends preserve descriptor signalling and fail closed."""
from __future__ import annotations

import ctypes
import errno
import os
import signal
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from basis import linux_pidfd as backend


class PidfdBackendTests(unittest.TestCase):
    def test_python_bindings_are_preferred_without_loading_libc(self):
        opening, sending = Mock(return_value=7), Mock()
        with patch.object(backend, "_PYTHON_OPEN", opening), patch.object(backend, "_PYTHON_SEND", sending), \
                patch.object(backend, "_libc") as libc:
            self.assertEqual(backend.pidfd_open(123), 7)
            backend.pidfd_send_signal(7, signal.SIGTERM)
        opening.assert_called_once_with(123, 0)
        sending.assert_called_once_with(7, signal.SIGTERM, None, 0)
        libc.assert_not_called()

    def test_missing_python_bindings_use_typed_libc_wrappers(self):
        opening, sending = Mock(return_value=7), Mock(return_value=0)
        library = SimpleNamespace(pidfd_open=opening, pidfd_send_signal=sending)
        with patch.object(backend, "_PYTHON_OPEN", None), patch.object(backend, "_PYTHON_SEND", None), \
                patch.object(backend, "_libc", return_value=library):
            self.assertEqual(backend.pidfd_open(123), 7)
            backend.pidfd_send_signal(7, signal.SIGTERM)
        opening.assert_called_once_with(123, 0)
        sending.assert_called_once_with(7, signal.SIGTERM, None, 0)
        self.assertEqual(opening.argtypes, [ctypes.c_int, ctypes.c_uint])
        self.assertEqual(sending.argtypes, [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint])

    def test_missing_python_and_libc_wrappers_use_known_linux_syscalls(self):
        for architecture in ("x86_64", "aarch64"):
            with self.subTest(architecture=architecture):
                syscall = Mock(side_effect=[7, 0])
                with patch.object(backend, "_PYTHON_OPEN", None), patch.object(backend, "_PYTHON_SEND", None), \
                        patch.object(backend, "_libc", return_value=SimpleNamespace(syscall=syscall)), \
                        patch.object(backend.platform, "machine", return_value=architecture):
                    self.assertEqual(backend.pidfd_open(123), 7)
                    backend.pidfd_send_signal(7, signal.SIGTERM)
                calls = [[value.value for value in call.args] for call in syscall.call_args_list]
                self.assertEqual(calls, [[434, 123, 0], [424, 7, int(signal.SIGTERM), None, 0]])

    def test_unknown_architecture_never_guesses_syscall_number(self):
        syscall = Mock()
        with patch.object(backend, "_PYTHON_OPEN", None), \
                patch.object(backend, "_libc", return_value=SimpleNamespace(syscall=syscall)), \
                patch.object(backend.platform, "machine", return_value="unknown-cpu"):
            with self.assertRaisesRegex(RuntimeError, "supports only"):
                backend.pidfd_open(123)
        syscall.assert_not_called()

    def test_32bit_abi_never_uses_64bit_syscall_numbers(self):
        syscall = Mock()
        with patch.object(backend, "_PYTHON_OPEN", None), \
                patch.object(backend, "_libc", return_value=SimpleNamespace(syscall=syscall)), \
                patch.object(backend.platform, "machine", return_value="x86_64"), \
                patch.object(backend.ctypes, "sizeof", return_value=4):
            with self.assertRaisesRegex(RuntimeError, "supports only"):
                backend.pidfd_open(123)
        syscall.assert_not_called()

    def test_kernel_enosys_propagates_without_weaker_fallback(self):
        def no_kernel_support(*args):
            ctypes.set_errno(errno.ENOSYS)
            return -1
        opening = Mock(side_effect=no_kernel_support)
        with patch.object(backend, "_PYTHON_OPEN", None), \
                patch.object(backend, "_libc", return_value=SimpleNamespace(pidfd_open=opening)):
            with self.assertRaises(OSError) as error:
                backend.pidfd_open(123)
        self.assertEqual(error.exception.errno, errno.ENOSYS)

    def test_esrch_becomes_process_lookup_error(self):
        def gone(*args):
            ctypes.set_errno(errno.ESRCH)
            return -1
        with patch.object(backend, "_PYTHON_SEND", None), \
                patch.object(backend, "_libc", return_value=SimpleNamespace(pidfd_send_signal=Mock(side_effect=gone))):
            with self.assertRaises(ProcessLookupError):
                backend.pidfd_send_signal(7, signal.SIGTERM)

    def test_probe_only_targets_self_with_signal_zero_and_closes_descriptor(self):
        with patch.object(backend, "pidfd_open", return_value=987) as opening, \
                patch.object(backend, "pidfd_send_signal") as sending, \
                patch.object(backend.os, "close") as close:
            backend.probe_pidfd_support()
        opening.assert_called_once_with(os.getpid())
        sending.assert_called_once_with(987, 0)
        close.assert_called_once_with(987)

    def test_probe_send_failure_closes_descriptor_and_fails_closed(self):
        with patch.object(backend, "pidfd_open", return_value=987), \
                patch.object(backend, "pidfd_send_signal", side_effect=OSError(errno.ENOSYS, "unsupported")), \
                patch.object(backend.os, "close") as close:
            with self.assertRaisesRegex(RuntimeError, "no target signals sent"):
                backend.probe_pidfd_support()
        close.assert_called_once_with(987)

    def test_non_linux_and_invalid_arguments_are_rejected_before_backend_calls(self):
        with patch.object(backend, "_PYTHON_OPEN") as opening:
            for pid in (0, -1, True, "123", 2**31):
                with self.assertRaises((ValueError, TypeError)):
                    backend.pidfd_open(pid)
            with patch.object(backend.sys, "platform", "darwin"):
                with self.assertRaisesRegex(RuntimeError, "requires Linux"):
                    backend.pidfd_open(123)
            opening.assert_not_called()


@unittest.skipUnless(sys.platform == "linux", "Linux required")
class CtypesPidfdIntegrationTests(unittest.TestCase):
    def run_real_child_check(self, raw_syscall=False):
        library = backend._libc()
        if raw_syscall:
            if backend.platform.machine().lower() not in {"x86_64", "aarch64"} or ctypes.sizeof(ctypes.c_void_p) != 8:
                self.skipTest("Raw syscall ABI unsupported")
            library = SimpleNamespace(syscall=library.syscall)
        with patch.object(backend, "_PYTHON_OPEN", None), patch.object(backend, "_PYTHON_SEND", None), \
                patch.object(backend, "_libc", return_value=library):
            try:
                backend.probe_pidfd_support()
            except RuntimeError as exc:
                cause = exc.__cause__
                if isinstance(cause, OSError) and cause.errno in {errno.ENOSYS, errno.EPERM, errno.EACCES}:
                    self.skipTest(str(exc))
                raise
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            descriptor = None
            try:
                descriptor = backend.pidfd_open(child.pid)
                backend.pidfd_send_signal(descriptor, signal.SIGTERM)
                self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)
                self.assertIsNone(unrelated.poll(), "Unrelated process was signalled")
                with self.assertRaises(ProcessLookupError):
                    backend.pidfd_send_signal(descriptor, signal.SIGTERM)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                for process in (child, unrelated):
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)

    def test_real_ctypes_backend_stops_only_descriptor_target(self):
        self.run_real_child_check()

    def test_real_raw_syscalls_stop_only_descriptor_target(self):
        self.run_real_child_check(raw_syscall=True)


if __name__ == "__main__":
    unittest.main()
