"""Local simulation admission policy; not broker authorization or quote attestation."""
from copy import deepcopy
from decimal import localcontext
import time

from trading_core.order_manager import _quantity


class PaperOrderRejected(ValueError):
    """A deterministic local preflight rejection; no order intent was submitted."""


def policy_values(max_quote_age_ms=None, max_order_notional=None):
    if (max_quote_age_ms is not None and
            (type(max_quote_age_ms) is not int or not 1 <= max_quote_age_ms <= 86400000)):
        raise ValueError('invalid_max_quote_age_ms')
    amount = None if max_order_notional is None else str(_quantity(max_order_notional, positive=True))
    return dict(max_quote_age_ms=max_quote_age_ms, max_order_notional=amount)


def validate_policy(value):
    if not isinstance(value, dict) or set(value) != {'max_quote_age_ms', 'max_order_notional'}:
        raise ValueError('invalid_paper_admission_policy')
    result = policy_values(**value)
    if result != value or all(v is None for v in result.values()):
        raise ValueError('noncanonical_paper_admission_policy')
    return result


def _wall_ms():
    return time.time_ns() // 1000000


def _mono_ms():
    return time.monotonic_ns() // 1000000


def _clocks():
    wall, mono = _wall_ms(), _mono_ms()
    if (type(wall) is not int or type(mono) is not int
            or not 0 <= wall < 2**63 or mono < 0):
        raise PaperOrderRejected('invalid_paper_quote_clock')
    return wall, mono


class QuoteAdmission:
    """Fresh receipts are handle-local; restored ticker marks never become receipts."""
    def __init__(self, policy=None):
        self._policy = policy_values() if policy is None else validate_policy(deepcopy(policy))
        self._receipts = {}

    @property
    def policy(self):
        return deepcopy(self._policy)

    def prepare_quote(self, symbol, price, timestamp, previous):
        limit = self._policy['max_quote_age_ms']
        if limit is None:
            return None
        wall, mono = _clocks()
        if timestamp <= 0 or timestamp > wall:
            raise PaperOrderRejected('paper_quote_future_or_zero_timestamp')
        if wall - timestamp > limit:
            raise PaperOrderRejected('paper_quote_stale')
        if previous is not None:
            old_stamp, old_price = previous['timestamp'], _quantity(previous['last'], positive=True)
            if timestamp < old_stamp or (timestamp == old_stamp and price != old_price):
                raise PaperOrderRejected('paper_quote_order_or_content_conflict')
            if timestamp == old_stamp:
                return None  # Same observation cannot renew a receipt, including after restart.
        old = self._receipts.get(symbol)
        if old is not None and (wall < old[2] or mono < old[3]):
            raise PaperOrderRejected('paper_quote_clock_rollback')
        return timestamp, price, wall, mono

    def accept_quote(self, symbol, receipt):
        # Called only AFTER the ticker write has committed successfully.
        if receipt is not None:
            self._receipts[symbol] = receipt

    def require_quote(self, symbol, tickers):
        limit = self._policy['max_quote_age_ms']
        if limit is None:
            return
        quote, receipt = tickers.get(symbol), self._receipts.get(symbol)
        if quote is None or receipt is None:
            raise PaperOrderRejected('paper_quote_refresh_required')
        stamp, price, received_wall, received_mono = receipt
        if quote['timestamp'] != stamp or _quantity(quote['last'], positive=True) != price:
            raise PaperOrderRejected('paper_quote_receipt_mismatch')
        wall, mono = _clocks()
        if wall < received_wall or mono < received_mono or stamp > wall:
            raise PaperOrderRejected('paper_quote_clock_rollback')
        # Include source age at receipt; wall-clock rollback cannot extend elapsed validity.
        age = max(wall - stamp, received_wall - stamp + mono - received_mono)
        if age > limit:
            raise PaperOrderRejected('paper_quote_stale')

    def require_notional(self, response):
        ceiling = self._policy['max_order_notional']
        if ceiling is not None:
            # Exact bounded product, not rounded display values or ambient Decimal context.
            with localcontext() as ctx:
                ctx.prec = 160
                notional = response['amount'] * response['average']
                if notional > _quantity(ceiling, positive=True):
                    raise PaperOrderRejected('paper_order_notional_limit')
