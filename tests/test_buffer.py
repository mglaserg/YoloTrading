import unittest

from crypto_yolo.buffer import buffered_destination


class BufferTests(unittest.TestCase):
    def test_no_trade_inside_relative_buffer(self):
        dest, inside = buffered_destination(0.096, 0.10, 0.05, "edge")
        self.assertTrue(inside)
        self.assertAlmostEqual(dest, 0.096)

    def test_edge_mode_trades_to_nearest_edge(self):
        dest, inside = buffered_destination(0.05, 0.10, 0.05, "edge")
        self.assertFalse(inside)
        self.assertAlmostEqual(dest, 0.095)

    def test_target_mode_matches_spreadsheet_trade_to_target(self):
        dest, inside = buffered_destination(0.05, 0.10, 0.05, "target")
        self.assertFalse(inside)
        self.assertAlmostEqual(dest, 0.10)


if __name__ == "__main__":
    unittest.main()
