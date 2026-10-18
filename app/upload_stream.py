"""Incremental multipart parsing for imports: file parts stream into consumers.

Starlette's form parser spools every file part to a temporary file before the
route runs, then the importer copies it again into its archive stage. Here each
file part is handed to its consumer while it arrives, with bounded buffering:
the parser waits whenever a consumer falls behind, so a request holds at most a
few megabytes in memory regardless of its size.
"""
from __future__ import annotations

import asyncio
from collections import deque

try:
    from python_multipart import MultipartParser
    from python_multipart.multipart import parse_options_header
except ImportError:  # python-multipart < 0.0.13
    from multipart import MultipartParser
    from multipart.multipart import parse_options_header

_COALESCE = 1 << 20        # hand consumers exactly 1 MiB at a time
_HIGH_WATER = 4 << 20      # parser waits above this much unread part data
MAX_FIELD_BYTES = 64 * 1024
MAX_FIELDS = 100


class MalformedUpload(ValueError):
    """The request body is not an acceptable multipart import."""


class UploadInterrupted(Exception):
    """The request body ended before this file part was complete."""


class StreamedUpload:
    """A file part read with UploadFile-like `await read(size)` semantics."""

    file = None   # no spooled source: ingest uses its async streaming path
    size = None   # unknown until the part ends

    def __init__(self, filename: str):
        self.filename = filename
        self._chunks = deque()
        self._buffered = 0
        self._eof = False
        self._error = None
        self._abandoned = False
        self._readable = asyncio.Event()
        self._writable = asyncio.Event()
        self._writable.set()

    async def feed(self, data: bytes) -> None:
        while self._buffered >= _HIGH_WATER and not self._abandoned:
            self._writable.clear()
            await self._writable.wait()
        if self._abandoned or not data:
            return
        self._chunks.append(data)
        self._buffered += len(data)
        self._readable.set()

    def finish(self, error: BaseException | None = None) -> None:
        self._eof, self._error = True, error
        self._readable.set()

    def abandon(self) -> None:
        """The consumer stopped reading; discard the rest of this part."""
        self._abandoned = True
        self._chunks.clear()
        self._buffered = 0
        self._writable.set()

    def _take(self, limit: int) -> bytes:
        chunk = self._chunks.popleft()
        if limit >= 0 and len(chunk) > limit:
            self._chunks.appendleft(chunk[limit:])
            chunk = chunk[:limit]
        self._buffered -= len(chunk)
        if self._buffered < _HIGH_WATER:
            self._writable.set()
        return chunk

    async def _available(self) -> bool:
        while not self._chunks:
            if self._eof:
                if self._error is not None:
                    raise UploadInterrupted('Upload ended before this file was complete.') from self._error
                return False
            self._readable.clear()
            await self._readable.wait()
        return True

    async def read(self, size: int = -1) -> bytes:
        """Read `size` bytes, or fewer only at the end of the part."""
        parts, have = [], 0
        while (size < 0 or have < size) and await self._available():
            parts.append(self._take(size - have if size >= 0 else -1))
            have += len(parts[-1])
        return parts[0] if len(parts) == 1 else b''.join(parts)

    async def read1(self, size: int = -1) -> bytes:
        """Return up to `size` bytes as soon as any arrive, without joining copies."""
        return self._take(size) if await self._available() else b''


def _decode(value: bytes, charset: str) -> str:
    try:
        return value.decode(charset)
    except (UnicodeDecodeError, LookupError):
        return value.decode('latin-1')


async def multipart_parts(request):
    """Yield ('field', name, text) and ('file', name, StreamedUpload) in body order.

    The caller must keep iterating: file data is delivered from inside this
    generator. A file part's upload is finished (or failed) before the next
    item is yielded or the generator raises.
    """
    content_type, params = parse_options_header(request.headers.get('content-type', ''))
    if content_type != b'multipart/form-data' or b'boundary' not in params:
        raise MalformedUpload('Expected a multipart/form-data upload.')
    charset = params.get(b'charset', b'utf-8')
    charset = charset.decode('latin-1') if isinstance(charset, bytes) else charset
    events = []
    header = {'name': b'', 'value': b''}
    disposition = [b'']

    def on_header_field(data, start, end):
        header['name'] += data[start:end]

    def on_header_value(data, start, end):
        header['value'] += data[start:end]

    def on_part_data(data, start, end):
        # Slices of the immutable request chunk stay zero-copy until coalesced;
        # parser-owned lookbehind buffers are copied before they can be reused.
        events.append(('data', memoryview(data)[start:end] if isinstance(data, bytes) else bytes(data[start:end])))

    def on_header_end():
        if header['name'].lower() == b'content-disposition':
            disposition[0] = header['value']
        header['name'] = header['value'] = b''

    callbacks = {
        'on_part_begin': lambda: disposition.__setitem__(0, b''),
        'on_header_field': on_header_field,
        'on_header_value': on_header_value,
        'on_header_end': on_header_end,
        'on_headers_finished': lambda: events.append(('headers', disposition[0])),
        'on_part_data': on_part_data,
        'on_part_end': lambda: events.append(('end', None)),
    }
    parser = MultipartParser(params[b'boundary'], callbacks)
    upload = name = None
    field = None
    pieces, pending = [], 0
    fields = 0
    try:
        async for chunk in request.stream():
            try:
                parser.write(chunk)
            except Exception as exc:
                raise MalformedUpload('Malformed multipart upload.') from exc
            for kind, value in events:
                if kind == 'headers':
                    _, options = parse_options_header(value)
                    if b'name' not in options:
                        raise MalformedUpload('A multipart part has no field name.')
                    name = _decode(options[b'name'], charset)
                    if b'filename' in options:
                        upload = StreamedUpload(_decode(options[b'filename'], charset))
                        pieces.clear()
                        pending = 0
                        yield 'file', name, upload
                    else:
                        fields += 1
                        if fields > MAX_FIELDS:
                            raise MalformedUpload('Too many form fields.')
                        field = bytearray()
                elif kind == 'data':
                    if upload is not None:
                        while value:
                            piece, value = value[:_COALESCE - pending], value[_COALESCE - pending:]
                            pieces.append(piece)
                            pending += len(piece)
                            if pending == _COALESCE:
                                await upload.feed(b''.join(pieces))
                                pieces.clear()
                                pending = 0
                    elif field is not None:
                        field += value
                        if len(field) > MAX_FIELD_BYTES:
                            raise MalformedUpload('A form field is too large.')
                elif upload is not None:
                    await upload.feed(b''.join(pieces))
                    pieces.clear()
                    pending = 0
                    upload.finish()
                    upload = None
                elif field is not None:
                    text, field = _decode(bytes(field), charset), None
                    yield 'field', name, text
            events.clear()
        try:
            parser.finalize()
        except Exception as exc:
            raise MalformedUpload('Malformed multipart upload.') from exc
        if upload is not None or field is not None:
            raise MalformedUpload('The multipart upload ended inside a part.')
    except BaseException as exc:
        if upload is not None:
            upload.finish(exc)
        raise
