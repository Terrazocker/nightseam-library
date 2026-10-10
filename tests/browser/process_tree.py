"""Own gate processes and descendants without targeting other applications."""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import time
from ctypes import wintypes


class ProcessTree:
    """A private Windows job (or POSIX process group) for one test launch."""

    def __init__(self, args, **kwargs):
        self.proc = None
        self.job = None
        self.stopped = False
        if os.name != "nt":
            self.proc = subprocess.Popen(args, start_new_session=True, **kwargs)
            return
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_api()
        self.job = self.api.CreateJobObjectW(None, None)
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            self._check(self.api.SetInformationJobObject(
                self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            # Frozen executables spawn a second process immediately. Assign the
            # suspended parent before any application code can create children.
            flags = kwargs.pop("creationflags", 0) | 0x4  # CREATE_SUSPENDED
            self.proc = subprocess.Popen(args, creationflags=flags, **kwargs)
            handle = self.api.OpenProcess(0x100 | 0x1, False, self.proc.pid)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                self._check(self.api.AssignProcessToJobObject(self.job, handle))
                self._resume_initial_thread()
            finally:
                self.api.CloseHandle(handle)
        except BaseException:
            try:
                if self.proc is not None:
                    self.proc.kill()
                    self.proc.wait(timeout=8)
            finally:
                self.api.CloseHandle(self.job)
                self.job = None
            raise

    @staticmethod
    def _check(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def _configure_api(self):
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "OpenThread": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result

    def _resume_initial_thread(self):
        snapshot = self.api.CreateToolhelp32Snapshot(0x4, 0)  # SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            available = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while available:
                if entry.th32OwnerProcessID == self.proc.pid:
                    thread = self._check(self.api.OpenThread(0x2, False, entry.th32ThreadID))
                    try:
                        if self.api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        self.api.CloseHandle(thread)
                    return
                available = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            raise RuntimeError("suspended gate process has no initial thread")
        finally:
            self.api.CloseHandle(snapshot)

    def stop(self, timeout=8):
        if self.proc is None or self.stopped:
            return
        if os.name == "nt":
            if self.job is not None:
                self._check(self.api.TerminateJobObject(self.job, 1))
                deadline = time.monotonic() + timeout
                while True:
                    state = _Accounting()
                    self._check(self.api.QueryInformationJobObject(
                        self.job, 1, ctypes.byref(state), ctypes.sizeof(state), None))
                    if state.ActiveProcesses == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("gate process tree did not exit")
                    time.sleep(0.05)
                self.api.CloseHandle(self.job)
                self.job = None
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.proc.wait(timeout=timeout)
        self.stopped = True


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IOCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD)]


class _Accounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD)]
