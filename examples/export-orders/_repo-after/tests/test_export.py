import unittest

from orders.export import to_csv
from orders.model import Order


class TestExport(unittest.TestCase):
    def test_header_only_when_empty(self):
        self.assertEqual(to_csv([]), "id,customer,status,amount_yuan\n")

    def test_amount_in_yuan(self):
        out = to_csv([Order(7, "张三", "paid", 12345)])
        self.assertEqual(out.splitlines()[1], "7,张三,paid,123.45")

    def test_comma_in_customer_is_quoted(self):
        out = to_csv([Order(8, "a,b", "paid", 100)])
        self.assertEqual(out.splitlines()[1], '8,"a,b",paid,1.00')


if __name__ == "__main__":
    unittest.main()
