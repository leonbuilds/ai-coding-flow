import unittest

from orders.model import Order
from orders.repo import InMemoryOrderRepo
from orders.service import OrderService


class TestService(unittest.TestCase):
    def test_count_by_status(self):
        svc = OrderService(InMemoryOrderRepo([Order(1, "a", "paid", 100), Order(2, "b", "created", 50)]))
        self.assertEqual(svc.count_by_status("paid"), 1)

    def test_export_csv_filters_by_status(self):
        svc = OrderService(InMemoryOrderRepo([Order(1, "a", "paid", 100), Order(2, "b", "created", 50)]))
        self.assertEqual(svc.export_csv("paid"), "id,customer,status,amount_yuan\n1,a,paid,1.00\n")


if __name__ == "__main__":
    unittest.main()
