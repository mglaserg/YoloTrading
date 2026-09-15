import unittest

from crypto_yolo.buffer import (
    absolute_buffer_bounds,
    buffered_destination,
)


class BufferTests(unittest.TestCase):
    def test_absolute_buffer_is_total_portfolio_weight_width(self):
        lo, hi = absolute_buffer_bounds(0.10, 0.02)
        self.assertAlmostEqual(lo, 0.09)
        self.assertAlmostEqual(hi, 0.11)

    def test_no_trade_inside_absolute_buffer(self):
        dest, inside = buffered_destination(0.096, 0.10, 0.02, "edge")
        self.assertTrue(inside)
        self.assertAlmostEqual(dest, 0.096)

    def test_edge_mode_trades_to_nearest_absolute_edge(self):
        dest, inside = buffered_destination(0.05, 0.10, 0.02, "edge")
        self.assertFalse(inside)
        self.assertAlmostEqual(dest, 0.09)

    def test_absolute_buffer_never_crosses_zero(self):
        lo, hi = absolute_buffer_bounds(0.005, 0.02)
        self.assertAlmostEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.015)
        lo, hi = absolute_buffer_bounds(-0.005, 0.02)
        self.assertAlmostEqual(lo, -0.015)
        self.assertAlmostEqual(hi, 0.0)

    def test_zero_target_has_no_buffer_and_flattens(self):
        self.assertEqual(absolute_buffer_bounds(0.0, 0.10), (0.0, 0.0))
        dest, inside = buffered_destination(0.004, 0.0, 0.10, "edge")
        self.assertFalse(inside)
        self.assertEqual(dest, 0.0)

    def test_target_mode_trades_to_target(self):
        dest, inside = buffered_destination(0.05, 0.10, 0.02, "target")
        self.assertFalse(inside)
        self.assertAlmostEqual(dest, 0.10)

    def test_legacy_relative_target_basis_is_available(self):
        dest, inside = buffered_destination(
            0.05, 0.10, 0.05, "edge", basis="relative_target"
        )
        self.assertFalse(inside)
        self.assertAlmostEqual(dest, 0.095)

    def test_negative_buffer_rejected(self):
        with self.assertRaises(ValueError):
            absolute_buffer_bounds(0.10, -0.01)


if __name__ == "__main__":
    unittest.main()
