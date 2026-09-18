from typing import List

from orders.model import Order


class InMemoryOrderRepo:
    def __init__(self, orders: List[Order]):
        self._orders = list(orders)

    def list_by_status(self, status: str) -> List[Order]:
        return [o for o in self._orders if o.status == status]
