"""Strict bounded JSON and the local PAPER worker's one-way framed protocol."""
import json
import re


MAX_FRAME_BYTES = 2048
MAX_SEQUENCE = 2**53 - 1
_MIN_INTEGER, _MAX_INTEGER = -(2**63), 2**63 - 1
_COMMON = {"v", "attempt", "role", "sequence", "event"}
_RESULT = {"exit_code", "terminal", "state", "reason"}
_EVENTS = {"guarded", "owned", "progress", "result"}
_CODES = {0, 2, 3, 4, 130, 143}
_ATTEMPT = re.compile(r"[0-9a-f]{32}\Z")
_LABEL = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")


class ProtocolError(ValueError):
    """The bounded local protocol is unavailable or invalid."""


def _invalid():
    return ProtocolError("paper_service_protocol_invalid")


def _limit(limit):
    if type(limit) is not int or limit <= 0:
        raise _invalid()


def _validate(value, limit, depth=0):
    if depth > 64:
        raise _invalid()
    kind = type(value)
    if value is None or kind is bool:
        return
    if kind is int:
        if not _MIN_INTEGER <= value <= _MAX_INTEGER:
            raise _invalid()
        return
    if kind is str:
        if len(value) > limit:
            raise _invalid()
        return
    if kind is list:
        if len(value) > limit:
            raise _invalid()
        for item in value:
            _validate(item, limit, depth + 1)
        return
    if kind is dict:
        if len(value) > limit:
            raise _invalid()
        for key, item in value.items():
            if type(key) is not str or len(key) > limit:
                raise _invalid()
            _validate(item, limit, depth + 1)
        return
    raise _invalid()


def encode_document(value, limit):
    """Encode exact JSON primitives, signed 64-bit integers and no floats."""
    _limit(limit)
    try:
        _validate(value, limit)
        encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, allow_nan=False)
        chunks, size = [], 0
        for chunk in encoder.iterencode(value):
            raw = chunk.encode("utf-8")
            size += len(raw)
            if size > limit:
                raise _invalid()
            chunks.append(raw)
        return b"".join(chunks)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _integer(raw):
    if len(raw) > 20:
        raise _invalid()
    value = int(raw)
    if not _MIN_INTEGER <= value <= _MAX_INTEGER:
        raise _invalid()
    return value


def _no_number(_raw):
    raise _invalid()


def decode_document(raw, limit):
    """Reject ambiguous, noncanonical, oversized or non-UTF-8 documents."""
    _limit(limit)
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise _invalid()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_int=_integer, parse_float=_no_number,
                           parse_constant=_no_number)
        if encode_document(value, limit) != raw:
            raise _invalid()
        return value
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _invalid() from None


def _identity(role, attempt):
    if (type(role) is not str or role not in {"capture", "stream"}
            or type(attempt) is not str or _ATTEMPT.fullmatch(attempt) is None):
        raise _invalid()


def _frame(value):
    if type(value) is not dict:
        raise _invalid()
    event = value.get("event")
    if type(event) is not str or event not in _EVENTS:
        raise _invalid()
    if set(value) != (_COMMON | _RESULT if event == "result" else _COMMON):
        raise _invalid()
    _identity(value["role"], value["attempt"])
    if (type(value["v"]) is not int or value["v"] != 1
            or type(value["sequence"]) is not int
            or not 0 <= value["sequence"] <= MAX_SEQUENCE):
        raise _invalid()
    if event == "result":
        if (type(value["exit_code"]) is not int or value["exit_code"] not in _CODES
                or type(value["terminal"]) is not bool):
            raise _invalid()
        for name in ("state", "reason"):
            item = value[name]
            if item is not None and (type(item) is not str or len(item) > 64
                                     or _LABEL.fullmatch(item) is None):
                raise _invalid()
    return value


def encode_frame(event, attempt, role, sequence, **result_fields):
    if set(result_fields) != (_RESULT if event == "result" else set()):
        raise _invalid()
    frame = dict(v=1, event=event, attempt=attempt, role=role, sequence=sequence,
                 **result_fields)
    _frame(frame)
    return encode_document(frame, MAX_FRAME_BYTES - 1) + b"\n"


class FrameReader:
    """Incremental framing with bounded pending bytes and a latched failure."""

    def __init__(self, role, attempt):
        _identity(role, attempt)
        self._role, self._attempt = role, attempt
        self._pending = bytearray()
        self._sequence = -1
        self._guarded = self._owned = self._failed = self._finished = False
        self._result = None

    @property
    def guarded(self):
        return self._guarded

    @property
    def owned(self):
        return self._owned

    @property
    def result(self):
        return None if self._result is None else dict(self._result)

    @property
    def sequence(self):
        return self._sequence

    def _accept(self, raw):
        frame = _frame(decode_document(raw, MAX_FRAME_BYTES - 1))
        if (frame["role"] != self._role or frame["attempt"] != self._attempt
                or frame["sequence"] != self._sequence + 1):
            raise _invalid()
        event = frame["event"]
        if not self._guarded:
            if event != "guarded":
                raise _invalid()
            self._guarded = True
        elif event == "guarded" or self._result is not None:
            raise _invalid()
        elif event == "owned":
            if self._owned:
                raise _invalid()
            self._owned = True
        elif event == "result":
            self._result = frame
        self._sequence = frame["sequence"]
        return frame

    def feed(self, data):
        try:
            if self._failed or type(data) is not bytes:
                raise _invalid()
            if data and (self._finished or self._result is not None):
                raise _invalid()
            frames, offset = [], 0
            while offset < len(data):
                if self._result is not None:
                    raise _invalid()
                end = data.find(b"\n", offset)
                if end < 0:
                    if len(self._pending) + len(data) - offset >= MAX_FRAME_BYTES:
                        raise _invalid()
                    self._pending.extend(data[offset:])
                    break
                if len(self._pending) + end - offset + 1 > MAX_FRAME_BYTES:
                    raise _invalid()
                raw = bytes(self._pending) + data[offset:end]
                self._pending.clear()
                frames.append(self._accept(raw))
                offset = end + 1
            return frames
        except ProtocolError:
            self._failed = True
            raise

    def finish(self):
        if self._failed or self._pending or self._result is None:
            self._failed = True
            raise _invalid()
        self._finished = True
