"""SHA-256 of file contents: Windows CNG when available, otherwise hashlib.

Python's bundled OpenSSL does not use the SHA extensions of many current x64
CPUs; Windows CNG does, about three times faster on the test machine. Imports,
deep health scans and library copies hash every byte, so this is their CPU
ceiling. Digests are identical: CNG is used only after it reproduces hashlib's
known-answer digests, and any load or self-check failure keeps hashlib. Short
identity hashes (session ids, tool provenance) stay on hashlib directly.
"""

from __future__ import annotations

import hashlib
import os
import threading

_lock = threading.Lock()
_factory = None
IMPLEMENTATION = None  # "cng" or "hashlib" once the first hash is created


def sha256(data=b""):
    """A new SHA-256 hasher with update(), digest(), hexdigest() and copy()."""
    hasher = (_factory or _select())()
    if data:
        hasher.update(data)
    return hasher


def _select():
    global _factory, IMPLEMENTATION
    with _lock:
        if _factory is None:
            factory = _load_cng() if os.name == "nt" else None
            _factory, IMPLEMENTATION = (factory, "cng") if factory else (hashlib.sha256, "hashlib")
    return _factory


def _load_cng():
    try:
        import ctypes
        from ctypes import wintypes
        bcrypt = ctypes.WinDLL("bcrypt")
        status = wintypes.LONG
        handle = ctypes.c_void_p
        bcrypt.BCryptOpenAlgorithmProvider.argtypes = (ctypes.POINTER(handle), wintypes.LPCWSTR,
                                                       wintypes.LPCWSTR, wintypes.ULONG)
        bcrypt.BCryptCreateHash.argtypes = (handle, ctypes.POINTER(handle), ctypes.c_void_p,
                                            wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG)
        bcrypt.BCryptHashData.argtypes = (handle, ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG)
        bcrypt.BCryptFinishHash.argtypes = (handle, ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG)
        bcrypt.BCryptDuplicateHash.argtypes = (handle, ctypes.POINTER(handle), ctypes.c_void_p,
                                               wintypes.ULONG, wintypes.ULONG)
        bcrypt.BCryptDestroyHash.argtypes = (handle,)
        for function in (bcrypt.BCryptOpenAlgorithmProvider, bcrypt.BCryptCreateHash, bcrypt.BCryptHashData,
                         bcrypt.BCryptFinishHash, bcrypt.BCryptDuplicateHash, bcrypt.BCryptDestroyHash):
            function.restype = status
        algorithm = handle()
        # The provider handle lives for the process; CNG allows concurrent
        # hash creation from it. Each hash object is used by one thread.
        if bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(algorithm), "SHA256", None, 0):
            return None
        destroy = bcrypt.BCryptDestroyHash
        _ULONG_MAX = 0xFFFFFFFF

        class CngSha256:
            name = "sha256"
            digest_size = 32
            block_size = 64

            def __init__(self, _handle=None):
                self._handle = _handle or handle()
                self._value = None
                if _handle is None and bcrypt.BCryptCreateHash(algorithm, ctypes.byref(self._handle),
                                                              None, 0, None, 0, 0):
                    raise OSError("Windows could not create a SHA-256 hash.")

            def update(self, data):
                if self._value is not None:
                    raise ValueError("SHA-256 hash already finished.")
                if type(data) is not bytes:
                    view = memoryview(data).cast("B")
                    data = view if not view.readonly else bytes(view)
                size = len(data)
                if not size:
                    return
                if size > _ULONG_MAX:
                    view = memoryview(data)
                    for start in range(0, size, 1 << 30):
                        self.update(bytes(view[start:start + (1 << 30)]))
                    return
                if type(data) is bytes:
                    # The bytes object stays alive for the call; no copy is made.
                    address = ctypes.cast(ctypes.c_char_p(data), ctypes.c_void_p)
                else:
                    address = ctypes.addressof((ctypes.c_char * size).from_buffer(data))
                if bcrypt.BCryptHashData(self._handle, address, size, 0):
                    raise OSError("Windows could not hash the data.")

            def digest(self):
                if self._value is None:
                    out = ctypes.create_string_buffer(32)
                    failed = bcrypt.BCryptFinishHash(self._handle, out, 32, 0)
                    destroy(self._handle)
                    self._handle = None
                    if failed:
                        raise OSError("Windows could not finish the SHA-256 hash.")
                    self._value = out.raw
                return self._value

            def hexdigest(self):
                return self.digest().hex()

            def copy(self):
                if self._value is not None:
                    raise ValueError("SHA-256 hash already finished.")
                duplicate = handle()
                if bcrypt.BCryptDuplicateHash(self._handle, ctypes.byref(duplicate), None, 0, 0):
                    raise OSError("Windows could not copy the SHA-256 hash.")
                return CngSha256(duplicate)

            def __del__(self):
                if getattr(self, "_handle", None):
                    destroy(self._handle)
                    self._handle = None

        # Known answers, including a multi-call stream and a copied state.
        for sample in (b"", b"abc", bytes(range(256)) * 4099):
            if _cng_digest(CngSha256, sample) != hashlib.sha256(sample).digest():
                return None
        partial = CngSha256()
        partial.update(b"night")
        branch = partial.copy()
        partial.update(bytearray(b"seam"))
        if (partial.digest() != hashlib.sha256(b"nightseam").digest() or
                branch.digest() != hashlib.sha256(b"night").digest()):
            return None
        return CngSha256
    except (OSError, AttributeError, ValueError):
        return None


def _cng_digest(factory, sample):
    hasher = factory()
    view = memoryview(sample)
    for start in range(0, len(sample), 65521):
        hasher.update(view[start:start + 65521])
    return hasher.digest()
