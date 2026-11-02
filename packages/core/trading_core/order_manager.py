"""Conservative order tracking for one asyncio event loop.

An adapter timeout or task cancellation is NOT a venue rejection. This module
never resubmits an uncertain intent. Optional state_store provides synchronous
write-ahead barriers. It is not a buying-power risk gate, broker-specific
reconciliation service or authority to trade live.
"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, localcontext
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Set

from trading_core.exchange import ExchangeInterface

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


def _identifier(value, name):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('invalid_' + name)
    return value


def _quantity(value, *, positive=False):
    if type(value) not in (str, int, float, Decimal) or len(str(value)) > 100:
        raise ValueError('invalid_order_number')
    try:
        result = Decimal(str(value))
        if (not result.is_finite() or result < 0 or (positive and result == 0)
                or result > Decimal('1e30') or result.as_tuple().exponent < -30
                or result.as_tuple().exponent > 30 or len(result.as_tuple().digits) > 60):
            raise ValueError('invalid_order_number')
        return result
    except DecimalException:
        raise ValueError('invalid_order_number') from None


def _remaining(amount, filled):
    with localcontext() as ctx:
        ctx.prec = 80
        return amount - filled


class OrderSide(Enum):
    BUY = 'buy'
    SELL = 'sell'


class OrderType(Enum):
    MARKET = 'market'
    LIMIT = 'limit'
    STOP_LIMIT = 'stop_limit'


class OrderStatus(Enum):
    PENDING = 'pending'
    SUBMITTING = 'submitting'
    UNKNOWN = 'unknown'
    OPEN = 'open'
    PARTIALLY_FILLED = 'partially_filled'
    CANCEL_PENDING = 'cancel_pending'
    FILLED = 'filled'
    CANCELED = 'canceled'
    REJECTED = 'rejected'
    EXPIRED = 'expired'


TERMINAL = frozenset((OrderStatus.FILLED, OrderStatus.CANCELED,
                      OrderStatus.REJECTED, OrderStatus.EXPIRED))
UNCERTAIN = frozenset((OrderStatus.PENDING, OrderStatus.SUBMITTING,
                       OrderStatus.UNKNOWN, OrderStatus.CANCEL_PENDING))


class ReconciliationRequired(RuntimeError):
    """Resolve existing uncertain outcomes before admitting another intent."""


class OrderPersistenceError(RuntimeError):
    """Local storage is uncertain. Stop I/O; reopen and reconcile, never retry blindly."""


class OrderOutcomeUnknown(RuntimeError):
    """No conclusive adapter result; inspect tracked state, do not resubmit."""


@dataclass
class Order:
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    amount: Decimal
    price: Optional[Decimal] = None
    status: OrderStatus = OrderStatus.PENDING
    filled: Decimal = field(default_factory=lambda: Decimal('0'))
    remaining: Decimal = field(default_factory=lambda: Decimal('0'))
    exchange_order_id: Optional[str] = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    strategy_id: Optional[str] = None
    cancel_requested: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return dict(client_order_id=self.client_order_id, symbol=self.symbol,
                    side=self.side.value, order_type=self.order_type.value,
                    amount=str(self.amount), price=str(self.price) if self.price is not None else None,
                    status=self.status.value, filled=str(self.filled), remaining=str(self.remaining),
                    exchange_order_id=self.exchange_order_id,
                    created_at=self.created_at.isoformat(), updated_at=self.updated_at.isoformat(),
                    strategy_id=self.strategy_id, cancel_requested=self.cancel_requested)


def _request(symbol, side, order_type, amount, price, strategy_id):
    _identifier(symbol, 'symbol')
    if not isinstance(side, OrderSide) or not isinstance(order_type, OrderType):
        raise ValueError('invalid_order_side_or_type')
    amount = _quantity(amount, positive=True)
    price = None if price is None else _quantity(price, positive=True)
    if order_type != OrderType.MARKET and price is None:
        raise ValueError('limit_price_required')
    if strategy_id is not None:
        _identifier(strategy_id, 'strategy_id')
    return symbol, side, order_type, amount, price, strategy_id


def _intent(order):
    return (order.symbol, order.side, order.order_type, order.amount, order.price, order.strategy_id)


class OrderManager:
    """One-event-loop manager; all returned records are detached snapshots.

    Calls that share an ID may inspect an in-flight snapshot but cannot submit
    twice. Different new IDs fail closed while any outcome remains uncertain.
    Per-order I/O exclusion prevents an older poll from overwriting a cancel.
    Do not share this object across threads or event loops.
    """

    def __init__(self, exchange: ExchangeInterface, *, state_store=None):
        self.exchange = exchange
        self._orders: Dict[str, Order] = {}
        self._pending_orders: Set[str] = set()
        self._inflight: Set[str] = set()
        self._lock = asyncio.Lock()
        self._polling_task: Optional[asyncio.Task] = None
        self._state_store = None
        self._storage_failed = False
        if state_store is not None:
            try:
                restored = state_store.claim()
                self.load_orders(restored)
                self._state_store = state_store
                self._persist()  # Persist UNKNOWN normalization before admitting I/O.
            except Exception:
                self._storage_failed = True
                raise OrderPersistenceError('order_state_restore_failed') from None

    @property
    def storage_failed(self) -> bool:
        """Latched until this manager is discarded; getters are diagnostic only."""
        return self._storage_failed

    def _ensure_storage(self):
        if self._storage_failed:
            raise OrderPersistenceError('order_storage_failed_restart_required')
        if self._state_store is not None:
            try:
                self._state_store.ensure_open()
            except Exception:
                self._storage_failed = True
                raise OrderPersistenceError('order_state_store_unavailable') from None

    def _persist(self):
        self._ensure_storage()
        if self._state_store is not None:
            try:
                self._state_store.save(self.get_all_orders())
            except Exception:
                self._storage_failed = True
                raise OrderPersistenceError('order_state_save_failed') from None
            except BaseException:
                self._storage_failed = True
                raise

    def _interrupted(self, order):
        # Preserve caller cancellation/interrupt even if recording UNKNOWN fails.
        try:
            self._unknown(order)
        except OrderPersistenceError:
            pass  # The manager's sticky storage failure still blocks all new I/O.

    async def start(self) -> None:
        self._ensure_storage()
        if self._polling_task is None or self._polling_task.done():
            self._polling_task = asyncio.create_task(self._poll_orders_loop())

    async def stop(self) -> None:
        task = self._polling_task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            finally:
                if self._polling_task is task:
                    self._polling_task = None

    def _unknown(self, order):
        # No await between state changes: task cancellation cannot strand a half
        # updated record. Keep the last validated venue ID and cumulative fills.
        order.status = OrderStatus.UNKNOWN
        order.updated_at = _now()
        self._pending_orders.add(order.client_order_id)
        self._persist()

    @staticmethod
    def _map_exchange_status(status: str) -> OrderStatus:
        values = {'open': OrderStatus.OPEN, 'closed': OrderStatus.FILLED,
                  'filled': OrderStatus.FILLED, 'canceled': OrderStatus.CANCELED,
                  'cancelled': OrderStatus.CANCELED, 'expired': OrderStatus.EXPIRED,
                  'rejected': OrderStatus.REJECTED, 'pending': OrderStatus.PENDING,
                  'partially_filled': OrderStatus.PARTIALLY_FILLED,
                  'cancel_pending': OrderStatus.CANCEL_PENDING}
        if not isinstance(status, str) or status.lower() not in values:
            raise ValueError('unrecognized_order_status')
        return values[status.lower()]

    def _apply_response(self, order, response, *, cancel_ack=False):
        """Validate the complete update before mutating anything.

        Required adapter fields: id, status, filled. Optional identity/amount/
        remaining fields must agree when supplied. Adapters must normalize their
        venue replies; a generic HTTP success is not a conclusive order outcome.
        """
        if not isinstance(response, dict):
            raise ValueError('invalid_order_response')
        venue_id = _identifier(response.get('id'), 'venue_order_id')
        if order.exchange_order_id is not None and venue_id != order.exchange_order_id:
            raise ValueError('venue_order_id_conflict')
        if any(other.client_order_id != order.client_order_id and other.exchange_order_id == venue_id
               for other in self._orders.values()):
            raise ValueError('duplicate_venue_order_id')
        for name, expected in (('clientOrderId', order.client_order_id), ('symbol', order.symbol),
                               ('side', order.side.value), ('type', order.order_type.value)):
            if name in response and response[name] != expected:
                raise ValueError('response_order_identity_conflict')
        filled = _quantity(response.get('filled'))
        if not order.filled <= filled <= order.amount:
            raise ValueError('invalid_cumulative_fill')
        remaining = _remaining(order.amount, filled)
        if 'amount' in response and _quantity(response['amount'], positive=True) != order.amount:
            raise ValueError('response_order_amount_conflict')
        if 'remaining' in response and _quantity(response['remaining']) != remaining:
            raise ValueError('response_remaining_conflict')
        target = self._map_exchange_status(response.get('status'))
        if target == OrderStatus.FILLED and remaining != 0:
            raise ValueError('incomplete_closed_order')
        if target in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED,
                      OrderStatus.PENDING, OrderStatus.CANCEL_PENDING) and remaining == 0:
            raise ValueError('complete_nonterminal_order')
        if target == OrderStatus.REJECTED and (filled != 0 or order.exchange_order_id is not None):
            # A rejected cancellation request must not reject the accepted order.
            # Generic post-ack rejection needs venue-specific reconciliation.
            raise ValueError('unproven_post_ack_rejection')
        if target in (OrderStatus.CANCELED, OrderStatus.EXPIRED) and remaining == 0:
            raise ValueError('full_fill_requires_filled_status')
        if target == OrderStatus.PARTIALLY_FILLED and filled == 0:
            raise ValueError('empty_partial_fill')
        if order.status in TERMINAL and (target != order.status or filled != order.filled):
            raise ValueError('terminal_order_changed')
        if target == OrderStatus.OPEN and filled > 0:
            target = OrderStatus.PARTIALLY_FILLED
        if (cancel_ack or order.cancel_requested) and target not in TERMINAL:
            target = OrderStatus.CANCEL_PENDING
        order.exchange_order_id = venue_id
        order.filled, order.remaining, order.status = filled, remaining, target
        order.updated_at = _now()
        if target in TERMINAL:
            order.cancel_requested = False
            self._pending_orders.discard(order.client_order_id)
        else:
            self._pending_orders.add(order.client_order_id)
        self._persist()

    async def place_order(self, symbol: str, side: OrderSide, order_type: OrderType,
                          amount: Decimal, price: Optional[Decimal] = None,
                          strategy_id: Optional[str] = None,
                          client_order_id: Optional[str] = None) -> Order:
        import uuid
        key = _identifier(client_order_id if client_order_id is not None
                          else 'order-' + uuid.uuid4().hex, 'client_order_id')
        intent = _request(symbol, side, order_type, amount, price, strategy_id)
        async with self._lock:
            self._ensure_storage()
            if key in self._orders:
                existing = self._orders[key]
                if _intent(existing) != intent:
                    raise ValueError('client_order_id_reused_with_different_intent')
                return deepcopy(existing)
            if any(o.status in UNCERTAIN for o in self._orders.values()):
                raise ReconciliationRequired('unresolved_order_outcome')
            order = Order(key, *intent[:4], price=intent[4], strategy_id=intent[5],
                          remaining=intent[3], status=OrderStatus.SUBMITTING)
            self._orders[key] = order
            self._pending_orders.add(key)
            self._persist()  # Commit intent before any adapter side effect.
            self._inflight.add(key)
        try:
            response = await self.exchange.create_order(
                symbol=order.symbol, order_type=order.order_type.value,
                side=order.side.value, amount=order.amount, price=order.price,
                params={'clientOrderId': key})
            self._apply_response(order, response)
        except asyncio.CancelledError:
            self._interrupted(order)
            raise  # Preserve structured cancellation; do not silently continue.
        except OrderPersistenceError:
            raise
        except Exception:
            self._unknown(order)
            logger.warning('order_submission_outcome_unknown')
            raise OrderOutcomeUnknown('submission_outcome_unknown') from None
        except BaseException:
            self._interrupted(order)
            raise
        finally:
            self._inflight.discard(key)
        return deepcopy(order)

    async def cancel_order(self, client_order_id: str) -> Order:
        key = _identifier(client_order_id, 'client_order_id')
        async with self._lock:
            self._ensure_storage()
            if key not in self._orders:
                raise ValueError('order_not_found')
            order = self._orders[key]
            if order.status in TERMINAL or key in self._inflight or order.status == OrderStatus.CANCEL_PENDING:
                return deepcopy(order)
            if order.status not in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED) or order.exchange_order_id is None:
                raise ReconciliationRequired('order_requires_readback_before_cancel')
            order.status = OrderStatus.CANCEL_PENDING
            order.cancel_requested = True
            order.updated_at = _now()
            self._persist()  # Cancellation request is durable before sending.
            self._inflight.add(key)
        try:
            response = await self.exchange.cancel_order(order.exchange_order_id, order.symbol)
            self._apply_response(order, response, cancel_ack=True)
        except asyncio.CancelledError:
            self._interrupted(order)
            raise
        except OrderPersistenceError:
            raise
        except Exception:
            self._unknown(order)
            logger.warning('order_cancellation_outcome_unknown')
            raise OrderOutcomeUnknown('cancellation_outcome_unknown') from None
        except BaseException:
            self._interrupted(order)
            raise
        finally:
            self._inflight.discard(key)
        return deepcopy(order)

    async def get_order(self, client_order_id: str) -> Optional[Order]:
        async with self._lock:
            return deepcopy(self._orders.get(client_order_id))

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Return all nonterminal intents, INCLUDING pending/unknown outcomes."""
        async with self._lock:
            return deepcopy([o for o in self._orders.values() if o.status not in TERMINAL
                             and (symbol is None or o.symbol == symbol)])

    async def get_orders_by_strategy(self, strategy_id: str) -> List[Order]:
        async with self._lock:
            return deepcopy([o for o in self._orders.values() if o.strategy_id == strategy_id])

    async def _poll_orders_loop(self) -> None:
        while True:
            await self._sync_orders()
            await asyncio.sleep(5)

    async def _sync_orders(self) -> None:
        self._ensure_storage()
        # Acquire ownership per order. Read-back never submits or cancels.
        for key in list(self._orders):
            async with self._lock:
                self._ensure_storage()
                order = self._orders[key]
                if key in self._inflight or order.status in TERMINAL or order.exchange_order_id is None:
                    continue
                self._inflight.add(key)
            try:
                response = await self.exchange.fetch_order(order.exchange_order_id, order.symbol)
                self._apply_response(order, response)
            except asyncio.CancelledError:
                self._interrupted(order)
                raise
            except OrderPersistenceError:
                raise
            except Exception:
                self._unknown(order)
                logger.warning('order_readback_unavailable')
            except BaseException:
                self._interrupted(order)
                raise
            finally:
                self._inflight.discard(key)

    def get_all_orders(self) -> Dict[str, Order]:
        """Detached diagnostic snapshot; never clear orders to bypass reconciliation."""
        return deepcopy(self._orders)

    def load_orders(self, orders: Dict[str, Order]) -> None:
        """Restore only into an unused manager, atomically validating all records.

        Active records become unknown until adapter read-back. Missing venue IDs
        cannot be reconciled generically. Never clear records to unlock trading.
        """
        if (self._state_store is not None or self._orders or self._inflight
                or self._polling_task is not None or not isinstance(orders, dict)):
            raise ValueError('restore_requires_unused_manager')
        clean, venue_ids = {}, set()
        for key, original in orders.items():
            _identifier(key, 'client_order_id')
            if not isinstance(original, Order) or original.client_order_id != key:
                raise ValueError('invalid_restored_order')
            item = deepcopy(original)
            intent = _request(*_intent(item))
            item.amount, item.price = intent[3:5]
            item.filled, item.remaining = _quantity(item.filled), _quantity(item.remaining)
            if (type(item.cancel_requested) is not bool
                    or not isinstance(item.status, OrderStatus) or item.filled > item.amount
                    or item.remaining != _remaining(item.amount, item.filled)
                    or (item.status == OrderStatus.FILLED and item.remaining != 0)
                    or (item.status == OrderStatus.REJECTED and item.filled != 0)
                    or (item.status in (OrderStatus.CANCELED, OrderStatus.EXPIRED) and item.remaining == 0)
                    or (item.status not in TERMINAL and item.remaining == 0)
                    or not isinstance(item.created_at, datetime)
                    or not isinstance(item.updated_at, datetime)):
                raise ValueError('invalid_restored_order')
            if item.exchange_order_id is not None:
                _identifier(item.exchange_order_id, 'venue_order_id')
                if item.exchange_order_id in venue_ids:
                    raise ValueError('duplicate_restored_venue_id')
                venue_ids.add(item.exchange_order_id)
            if item.status == OrderStatus.CANCEL_PENDING:
                item.cancel_requested = True
            if item.status not in TERMINAL:
                item.status = OrderStatus.UNKNOWN
            elif item.cancel_requested:
                raise ValueError('terminal_order_has_pending_cancel')
            clean[key] = item
        self._orders = clean
        self._pending_orders = {key for key, o in clean.items() if o.status not in TERMINAL}
