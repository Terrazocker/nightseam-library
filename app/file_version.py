"""Short-lived file versions for discardable IO caches, never integrity baselines."""
from contextlib import contextmanager
from functools import lru_cache
import os


@contextmanager
def pinned_version(path):
    """Pin Windows writers/renames and expose a handle-local NTFS/ReFS version.

    Unsupported filesystems and open writers disable caching. POSIX callers
    compare fstat before/after hashing; ctime detects restored mtime there.
    Windows timestamps can collide after rapid writes with restored mtime.
    Versions may be reused only while the same writer-excluding handle is open.
    """
    if os.name != 'nt':
        try:
            stream = path.open('rb')
        except OSError:
            yield None
            return
        try:
            def version():
                info = os.fstat(stream.fileno())
                current = path.stat()
                if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                    return None
                return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
            yield version
        finally:
            stream.close()
        return

    import ctypes
    kernel, Basic, Identity, Standard = _windows_api()

    # Attribute-only handles do not participate in Windows read/write sharing
    # arbitration. GENERIC_READ is essential to exclude an open/new writer.
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        yield None
        return
    try:
        filesystem = ctypes.create_unicode_buffer(32)
        if not kernel.GetVolumeInformationByHandleW(handle, None, 0, None, None, None, filesystem, 32) or filesystem.value not in ('NTFS', 'ReFS'):
            yield None
            return

        lifetime = object()

        def version():
            basic, identity, standard = Basic(), Identity(), Standard()
            if not all(kernel.GetFileInformationByHandleEx(handle, kind, ctypes.byref(info), ctypes.sizeof(info))
                       for kind, info in ((0, basic), (18, identity), (1, standard))):
                return None
            if basic.attributes & (0x400 | 0x10) or standard.deleted:
                return None
            return lifetime, identity.volume, bytes(identity.id), standard.size, basic.written, basic.changed
        yield version
    finally:
        kernel.CloseHandle(handle)


@lru_cache(maxsize=1)
def _windows_api():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.GetVolumeInformationByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]

    class Basic(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in ('created', 'accessed', 'written', 'changed')] + [('attributes', wintypes.DWORD)]

    class Identity(ctypes.Structure):
        _fields_ = [('volume', ctypes.c_ulonglong), ('id', ctypes.c_ubyte * 16)]

    class Standard(ctypes.Structure):
        _fields_ = [('allocated', ctypes.c_longlong), ('size', ctypes.c_longlong),
            ('links', wintypes.DWORD), ('deleted', ctypes.c_ubyte), ('directory', ctypes.c_ubyte)]

    return kernel, Basic, Identity, Standard
