import unittest

from crypto_yolo.reconcile_orders import _extract_status


class OrderReconcileTests(unittest.TestCase):
    def test_direct_order_status_shape(self):
        status, oid = _extract_status({"status": "open", "order": {"oid": 123}})
        self.assertEqual(status, "open")
        self.assertEqual(oid, 123)

    def test_documented_wrapper_shape(self):
        payload = {"status": "order", "order": {"status": "filled", "order": {"oid": 456}}}
        status, oid = _extract_status(payload)
        self.assertEqual(status, "filled")
        self.assertEqual(oid, 456)


if __name__ == "__main__":
    unittest.main()
