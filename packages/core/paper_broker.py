"""In-memory paper order adapter for interface tests; does not simulate fills."""
try:
    from .broker_interface import Broker, Environment, OrderReceipt, OrderRequest, require_paper
except ImportError:
    from broker_interface import Broker, Environment, OrderReceipt, OrderRequest, require_paper


class PaperBroker:
    environment = Environment.PAPER

    def __init__(self):
        self._orders = {}

    def list_instruments(self):
        return []

    def submit_limit(self, request: OrderRequest) -> OrderReceipt:
        require_paper(self.environment)
        existing = self._orders.get(request.client_order_id)
        if existing:
            old_request, receipt = existing
            if old_request != request:
                raise ValueError("idempotency key reused with different payload")
            return receipt
        receipt = OrderReceipt(request.client_order_id, "paper:" + request.client_order_id, "accepted")
        self._orders[request.client_order_id] = (request, receipt)
        return receipt

    def get_order(self, venue_order_id: str) -> OrderReceipt:
        for _, receipt in self._orders.values():
            if receipt.venue_order_id == venue_order_id:
                return receipt
        raise KeyError(venue_order_id)

    def cancel(self, venue_order_id: str) -> OrderReceipt:
        for key, (request, receipt) in self._orders.items():
            if receipt.venue_order_id == venue_order_id:
                updated = OrderReceipt(receipt.client_order_id, venue_order_id, "cancelled")
                self._orders[key] = (request, updated)
                return updated
        raise KeyError(venue_order_id)
