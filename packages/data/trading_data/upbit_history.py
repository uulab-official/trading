"""Explicit, bounded public OHLCV acquisition; never an order/broker adapter.

Only the import CLI calls this network client. Replay consumes immutable local
files. Provider metadata is self-reported, not cryptographic authentication.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

UNITS = (1, 3, 5, 10, 15, 30, 60, 240)
MAX_BARS = 10000
MAX_PAGE_BYTES = 1024 * 1024
MAX_FIXTURE_BYTES = 8 * 1024 * 1024
CLOSE_MARGIN_MS = 5000
ENDPOINT = 'https://api.upbit.com/v1/candles/minutes/'
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
PROVENANCE_FIELDS = {'schema_version', 'provider', 'market', 'unit_minutes',
                     'end_exclusive_ms', 'retrieved_at', 'bar_count',
                     'candles_sha256', 'gap_policy', 'missing_intervals', 'authenticated'}


class MarketDataUnavailable(RuntimeError):
    """Acquisition failed. No partial fixture should be published."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError('non-finite JSON value')


def decode_json(raw, *, decimal_numbers=True):
    try:
        return json.loads(raw, parse_float=Decimal if decimal_numbers else float, parse_constant=_reject_constant,
                          object_pairs_hook=_unique_object)
    except (UnicodeError, RecursionError, DecimalException) as error:
        raise ValueError('invalid JSON data') from error


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError('invalid integer')
    return value


def _milliseconds(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('explicit timezone required')
    delta = value.astimezone(timezone.utc) - EPOCH
    result = delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000
    return _integer(result, 0, 253402300799999)


def parse_end(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('ISO 8601 end time required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _milliseconds(result)
    return result


def _iso(milliseconds):
    return (EPOCH + timedelta(milliseconds=milliseconds)).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _market(value):
    if not isinstance(value, str) or not re.fullmatch(r'KRW-[A-Z0-9]{1,20}', value) or value == 'KRW-KRW':
        raise ValueError('this importer supports KRW spot pairs only')
    return value


def _unit(value):
    if type(value) is not int or value not in UNITS:
        raise ValueError('unsupported minute unit')
    return value


def timeframe(unit):
    _unit(unit)
    return {60: '1h', 240: '4h'}.get(unit, str(unit) + 'm')


def _decimal_text(value, *, positive=True):
    if type(value) not in (str, int, Decimal) or len(str(value)) > 100:
        raise ValueError('decimal text or exact JSON number required')
    try:
        number = Decimal(value)
        if (not number.is_finite() or number < 0 or (positive and number == 0)
                or number > Decimal('1e18') or number.as_tuple().exponent < -18
                or len(number.as_tuple().digits) > 37):
            raise ValueError('invalid price or volume')
        text = format(number, 'f')
        return (text.rstrip('0').rstrip('.') if '.' in text else text) if number else '0'
    except DecimalException as error:
        raise ValueError('invalid decimal') from error


def _row(timestamp, numbers):
    values = [_decimal_text(value, positive=index < 4) for index, value in enumerate(numbers)]
    op, high, low, close = map(Decimal, values[:4])
    if not low <= op <= high or not low <= close <= high:
        raise ValueError('invalid OHLC range')
    return [timestamp, *values]


def _provider_row(item, market, unit, cursor):
    if not isinstance(item, dict) or item.get('market') != market or type(item.get('unit')) is not int or item['unit'] != unit:
        raise ValueError('mismatched provider market or unit')
    opening = item.get('candle_date_time_utc')
    if not isinstance(opening, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', opening):
        raise ValueError('invalid UTC candle opening time')
    timestamp = _milliseconds(datetime.fromisoformat(opening).replace(tzinfo=timezone.utc))
    if timestamp % (unit * 60000) or timestamp >= cursor:
        raise ValueError('unclosed candle or non-progressing pagination')
    fields = ('opening_price', 'high_price', 'low_price', 'trade_price', 'candle_acc_trade_volume')
    try:
        return _row(timestamp, [item[k] for k in fields])
    except KeyError as error:
        raise ValueError('incomplete OHLCV row') from error


def candles_hash(rows):
    return hashlib.sha256(json.dumps(rows, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_market_fixture(fixture):
    """Recheck imported file before replay; never infer or fill missing bars."""
    try:
        if not isinstance(fixture, dict) or fixture.get('mode') != 'paper':
            raise ValueError('paper market fixture required')
        p = fixture['data_provenance']
        if not isinstance(p, dict) or set(p) != PROVENANCE_FIELDS:
            raise ValueError('unsupported provenance fields')
        if (type(p['schema_version']) is not int or p['schema_version'] != 1
                or p['provider'] != 'upbit_public_rest' or p['authenticated'] is not False
                or p['gap_policy'] != 'reject' or type(p['missing_intervals']) is not int or p['missing_intervals'] != 0):
            raise ValueError('unsupported provenance')
        market, unit = _market(p['market']), _unit(p['unit_minutes'])
        count = _integer(p['bar_count'], 1, MAX_BARS)
        end = _integer(p['end_exclusive_ms'], 1, 253402300799999)
        if end % (unit * 60000) or end > _milliseconds(parse_end(p['retrieved_at'])) - CLOSE_MARGIN_MS:
            raise ValueError('invalid closed-history boundary')
        if fixture.get('symbol') != market[4:] + '/KRW' or fixture.get('timeframe') != timeframe(unit):
            raise ValueError('fixture market configuration mismatch')
        rows = fixture['candles']
        if not isinstance(rows, list) or len(rows) != count:
            raise ValueError('bar count mismatch')
        start = end - count * unit * 60000
        for i, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != 6:
                raise ValueError('six-column OHLCV required')
            _integer(row[0], 0, 253402300799999)
            if row[0] != start + i * unit * 60000:
                raise ValueError('market history gap; no candle filling is permitted')
            if row != _row(row[0], row[1:]):
                raise ValueError('noncanonical decimal data')
        if not isinstance(p['candles_sha256'], str) or p['candles_sha256'] != candles_hash(rows):
            raise ValueError('market history checksum mismatch')
        return dict(p)
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError('invalid market fixture') from error


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(newurl, code, 'redirect not permitted', headers, fp)


def public_get(url):
    """The only network boundary: bounded GET with normal TLS verification."""
    parsed = urlsplit(url)
    match = re.fullmatch(r'/v1/candles/minutes/([0-9]+)', parsed.path)
    if parsed.scheme != 'https' or parsed.netloc != 'api.upbit.com' or not match or parsed.fragment:
        raise ValueError('unsupported public market endpoint')
    _unit(int(match[1]))
    query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
    if set(query) != {'market', 'count', 'to'} or any(len(v) != 1 for v in query.values()):
        raise ValueError('unsupported market query')
    _market(query['market'][0]); _integer(int(query['count'][0]), 1, 200); parse_end(query['to'][0])
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'uulab-history/1'}, method='GET')
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        if response.status != 200 or response.headers.get_content_type() != 'application/json':
            raise MarketDataUnavailable('unexpected public response')
        body = response.read(MAX_PAGE_BYTES + 1)
        if len(body) > MAX_PAGE_BYTES:
            raise ValueError('market response too large')
        return body, dict(response.headers.items())


class UpbitHistoryClient:
    def __init__(self, *, get=None, sleep=time.sleep, clock=None):
        self._get = get or public_get
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _page(self, url, count):
        for attempt in range(3):
            self._sleep(0.2)  # Conservative per-client pacing; shared-IP limits still apply.
            try:
                body, headers = self._get(url)
            except HTTPError as error:
                code, headers = error.code, error.headers or {}
                error.close()
                if code == 418 or code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise MarketDataUnavailable('public endpoint rejected request') from None
                retry = next((v for k, v in headers.items() if k.lower() == 'retry-after'), None)
                try:
                    delay = float(retry) if retry is not None else float(2 ** attempt)
                except (ValueError, TypeError):
                    raise MarketDataUnavailable('unrecognized server cooldown; retry manually later') from None
                if not math.isfinite(delay) or delay < 0 or delay > 30:
                    raise MarketDataUnavailable('retry later; server cooldown exceeds this run') from None
                self._sleep(max(1.0, delay))
                continue
            except (URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise MarketDataUnavailable('public market connection unavailable') from None
                self._sleep(float(2 ** attempt))
                continue
            if not isinstance(body, bytes) or len(body) > MAX_PAGE_BYTES:
                raise ValueError('invalid or oversized market response')
            page = decode_json(body)
            if not isinstance(page, list) or len(page) > count:
                raise ValueError('unexpected candle response')
            remaining = next((v for k, v in headers.items() if k.lower() == 'remaining-req'), '')
            if re.search(r'(?:^|;)\s*sec\s*=\s*0\s*(?:;|$)', remaining):
                self._sleep(1.0)
            return page
        raise MarketDataUnavailable('retry limit exceeded')

    def download(self, market, *, unit=60, count=720, end=None):
        market, unit = _market(market), _unit(unit)
        _integer(count, 1, MAX_BARS)
        now = _milliseconds(self._clock())
        safe_end = now - CLOSE_MARGIN_MS
        requested = safe_end if end is None else _milliseconds(end)
        if requested > safe_end:
            raise ValueError('end must precede the close safety margin')
        boundary = requested - requested % (unit * 60000)
        if boundary - count * unit * 60000 < 0:
            raise ValueError('history precedes epoch')
        collected, cursor = {}, boundary
        for _ in range(100):  # Bounded even if the provider sends tiny/duplicate pages.
            page_size = min(200, count - len(collected))
            url = ENDPOINT + str(unit) + '?' + urlencode({'market': market, 'count': page_size, 'to': _iso(cursor)})
            page = self._page(url, page_size)
            if not page:
                raise ValueError('insufficient market history')
            rows = [_provider_row(item, market, unit, cursor) for item in page]
            for row in rows:
                if row[0] in collected and row != collected[row[0]]:
                    raise ValueError('conflicting duplicate candle')
                collected[row[0]] = row
            cursor = min(row[0] for row in rows)
            if len(collected) == count:
                break
        else:
            raise MarketDataUnavailable('pagination limit exceeded')
        rows = [collected[key] for key in sorted(collected)]
        retrieved_at = self._clock()
        _milliseconds(retrieved_at)  # A changed/naive clock cannot silently inherit local timezone.
        p = dict(schema_version=1, provider='upbit_public_rest', market=market,
                 unit_minutes=unit, end_exclusive_ms=boundary,
                 retrieved_at=retrieved_at.astimezone(timezone.utc).isoformat(), bar_count=len(rows),
                 candles_sha256=candles_hash(rows), gap_policy='reject', missing_intervals=0,
                 authenticated=False)
        fixture = dict(mode='paper', symbol=market[4:] + '/KRW', timeframe=timeframe(unit),
                       candles=rows, data_provenance=p)
        validate_market_fixture(fixture)
        return fixture


def check_destination(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('fixture symlinks are not supported')
    if path.exists():
        raise FileExistsError('choose a new fixture filename')
    if not path.parent.is_dir():
        raise ValueError('output directory must exist')
    return path


def write_fixture(path, fixture):
    """Atomic create-if-absent. No partial file, overwrite or auto-update."""
    path = check_destination(path)
    validate_market_fixture(fixture)
    body = json.dumps(fixture, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > MAX_FIXTURE_BYTES:
        raise ValueError('fixture too large')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.market-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)  # Atomic publication without replacing a racing writer.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
