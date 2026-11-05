"""Explicit read-only order reconciliation; never submits or cancels an order.

This package service shares OrderManager's per-order I/O exclusion, validation
and optional durable-store barriers. No polling behavior or live adapter is
changed. An adapter must query the SAME account/venue as the original intent.
A missing lookup result is inconclusive, not proof of rejection/nonacceptance.
"""
import asyncio
from copy import deepcopy
import logging
import math

from trading_core.order_manager import (
    OrderManager, OrderOutcomeUnknown, OrderPersistenceError, OrderType,
    ReconciliationRequired, TERMINAL, _identifier, _quantity,
)

logger = logging.getLogger(__name__)


def _validate_client_binding(order, response):
    """Require positive identity evidence before learning a previously unknown ID."""
    required = {'id', 'clientOrderId', 'symbol', 'side', 'type', 'amount',
                'remaining', 'filled', 'status'}
    if not isinstance(response, dict) or not required <= response.keys():
        raise ValueError('incomplete_client_order_lookup')
    # Existing _apply_response checks all echoed identities, amount, remaining,
    # monotonic fills, unique venue ID and legal status before any mutation.
    # A market response's price may be its execution price, not a limit price.
    if order.order_type != OrderType.MARKET:
        if _quantity(response.get('price'), positive=True) != order.price:
            raise ValueError('lookup_limit_price_conflict')


async def reconcile_order(manager: OrderManager, client_order_id: str, *,
                          allow_client_lookup: bool = False,
                          timeout_seconds: float = 10.0):
    """Read one existing order and persist a validated outcome before returning.

    Known venue IDs always use fetch_order(id, symbol). Missing venue IDs require
    explicit opt-in and a callable fetch_order_by_client_id(client_id, symbol)
    on the same adapter. That capability must include closed orders; do not
    implement it using an open-orders-only list or guessed venue IDs.

    No retries or automatic ID lookup. A timeout/caller cancellation requests
    cancellation of the read task and discards late results. Ownership is held
    until that task finishes. Blocking adapters can still block the event loop;
    this is not a hard real-time deadline. See docs.
    """
    if not isinstance(manager, OrderManager):
        raise ValueError('order_manager_required')
    key = _identifier(client_order_id, 'client_order_id')
    if (type(allow_client_lookup) is not bool
            or type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30):
        raise ValueError('invalid_reconciliation_options')
    async with manager._lock:
        manager._ensure_storage()
        if key not in manager._orders:
            raise ValueError('order_not_found')
        order = manager._orders[key]
        if key in manager._inflight:
            raise ReconciliationRequired('order_io_in_progress')
        if order.status in TERMINAL:
            return deepcopy(order)
        exchange = manager.exchange
        by_client = order.exchange_order_id is None
        if by_client:
            if not allow_client_lookup:
                raise ReconciliationRequired('client_lookup_requires_explicit_opt_in')
            method = getattr(exchange, 'fetch_order_by_client_id', None)
            if not callable(method):
                raise ReconciliationRequired('client_lookup_not_supported')
            args = (key, order.symbol)
        else:
            method = exchange.fetch_order
            args = (order.exchange_order_id, order.symbol)
        manager._inflight.add(key)
    read_task = None
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        # Shield the child so its cancellation suppression cannot turn caller
        # cancellation into a successful outcome (including on Python 3.13).
        read_task = asyncio.ensure_future(method(*args))
        response = await asyncio.wait_for(asyncio.shield(read_task), timeout=timeout_seconds)
        if loop.time() >= deadline or manager.exchange is not exchange:
            raise ValueError('late_or_foreign_reconciliation_result')
        manager._ensure_storage()
        if by_client:
            _validate_client_binding(order, response)
        manager._apply_response(order, response)  # Reuse atomic validation + persistence.
    except asyncio.CancelledError:
        manager._interrupted(order)
        raise
    except OrderPersistenceError:
        raise
    except Exception:
        manager._unknown(order)
        logger.warning('order_reconciliation_inconclusive')
        raise OrderOutcomeUnknown('reconciliation_inconclusive') from None
    except BaseException:
        manager._interrupted(order)
        raise
    finally:
        if read_task is not None and not read_task.done():
            def finish_read(task):
                # Consume exceptions without exposing raw adapter text; NEVER
                # apply a late result. Retain exclusion until read I/O stops.
                if not task.cancelled():
                    task.exception()
                manager._inflight.discard(key)
            read_task.add_done_callback(finish_read)
            read_task.cancel()
        else:
            if read_task is not None and not read_task.cancelled():
                read_task.exception()
            manager._inflight.discard(key)
    return deepcopy(order)
