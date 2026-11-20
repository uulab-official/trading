"""Deterministic in-memory broker SANDBOX for order lifecycle testing.

No credentials, sockets, HTTP, exchange accounts, or live order methods.
This adapter is for fault-injection and accounting-contract experiments only.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import re


def _positive(value, label, *, allow_zero=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError) as error:
        raise ValueError(label + " must be a finite decimal") from error
    if not number.is_finite() or (number < 0 if allow_zero else number <= 0):
        raise ValueError(label + " must be a finite positive decimal")
    return number


class SimulatedLostAcknowledgment(RuntimeError):
    """Submission may have succeeded; never replay the original request."""


class SandboxOrderEmulator:
    """Deterministic broker test double with partial fill and lost-ACK injection.

    Order state is in-memory; losing this instance destroys test broker state.
    The durable journal is separate and must be queried with external
    authenticated evidence in a real integration.
    """

    def __init__(self):
        self.orders = {}
        self.next_id = 1

    def submit_once(self, client_id, symbol, side, quantity, *,
                    lose_ack=False):
        if not isinstance(client_id, str) or not client_id:
            raise ValueError("Client ID required")
        if client_id in self.orders:
            raise ValueError("Duplicate submission is forbidden; query order state")
        if side not in ("buy", "sell"):
            raise ValueError("Spot side must be buy or sell")
        if not isinstance(symbol, str) or not re.fullmatch(
            r"[A-Z0-9]{2,20}/(?:KRW|USDT)", symbol
        ):
            raise ValueError("Invalid spot market")
        if symbol.split("/")[0] == symbol.split("/")[1]:
            raise ValueError("Self-quoted spot market forbidden")
        qty = _positive(quantity, "quantity")
        order_id = "sandbox-" + str(self.next_id)
        self.next_id += 1
        self.orders[client_id] = {
            "order_id": order_id, "client_id": client_id,
            "symbol": symbol, "side": side,
            "quantity": str(qty), "filled": "0",
            "gross_quote": "0", "fees_quote": "0",
            "status": "open",
        }
        if lose_ack:
            raise SimulatedLostAcknowledgment("Synthetic lost ACK, broker may have accepted")
        return self.query(client_id)

    def query(self, client_id):
        if client_id not in self.orders:
            return None
        return dict(self.orders[client_id])

    def fill(self, client_id, quantity, *, price, fee_pct=0):
        row = self.orders.get(client_id)
        if row is None or row["status"] not in ("open", "partial"):
            raise ValueError("Only an open sandbox order can receive a fill")
        qty = _positive(quantity, "fill quantity")
        rate = _positive(price, "fill price")
        fee = _positive(fee_pct, "fee percentage", allow_zero=True)
        if fee >= 100:
            raise ValueError("Sandbox fee percentage must be below 100")
        current = Decimal(row["filled"])
        requested = Decimal(row["quantity"])
        if current + qty > requested:
            raise ValueError("Partial fill exceeds ordered quantity")
        gross = qty * rate
        row["filled"] = str(current + qty)
        row["gross_quote"] = str(Decimal(row["gross_quote"]) + gross)
        row["fees_quote"] = str(Decimal(row["fees_quote"]) + gross * fee / 100)
        row["status"] = "filled" if current + qty == requested else "partial"
        return self.query(client_id)

    def cancel(self, client_id):
        row = self.orders.get(client_id)
        if row is None or row["status"] not in ("open", "partial"):
            raise ValueError("Cannot cancel nonexistent or closed sandbox order")
        row["status"] = "canceled"
        return self.query(client_id)

    @staticmethod
    def evidence(snapshot):
        """Immutable-test-record hash. NOT authenticated external proof."""
        if not isinstance(snapshot, dict) or not snapshot.get("order_id"):
            raise ValueError("Sandbox snapshot required")
        content = "|".join(str(snapshot[key]) for key in
                           ("order_id", "client_id", "symbol", "side", "quantity",
                            "filled", "gross_quote", "fees_quote", "status"))
        return "sim-" + hashlib.sha256(content.encode()).hexdigest()[:40]
