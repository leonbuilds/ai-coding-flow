import csv
import io
from typing import List

from orders.model import Order


def to_csv(orders: List[Order]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "customer", "status", "amount_yuan"])
    for o in orders:
        w.writerow([o.id, o.customer, o.status, f"{o.amount_cents / 100:.2f}"])
    return buf.getvalue()
