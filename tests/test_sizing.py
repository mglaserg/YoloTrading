import tempfile
from pathlib import Path
import unittest

from crypto_yolo.config import YoloConfig
from crypto_yolo.sizing import SizingError, SizingLedger


class SizingLedgerTests(unittest.TestCase):
    def _config(self, *, nominal=50_000.0, min_mult=0.25, max_mult=3.0, subaccount="0xyolo"):
        return YoloConfig(
            nominal_usd=nominal,
            sizing_mode="compound",
            min_nominal_multiplier=min_mult,
            max_nominal_multiplier=max_mult,
            hl_subaccount_address=subaccount,
        )

    def test_initialization_and_profit_compound_nominal(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = SizingLedger(Path(td) / "yolo.sqlite")
            cfg = self._config()
            first = ledger.decision(account_value_usd=20_000.0, config=cfg)
            self.assertTrue(first.initialized_now)
            self.assertAlmostEqual(first.applied_multiplier, 1.0)
            self.assertAlmostEqual(first.effective_nominal_usd, 50_000.0)

            second = ledger.decision(account_value_usd=22_000.0, config=cfg)
            self.assertAlmostEqual(second.nav_per_unit, 1.1)
            self.assertAlmostEqual(second.raw_multiplier, 1.1)
            self.assertAlmostEqual(second.effective_nominal_usd, 55_000.0)

    def test_external_deposit_does_not_create_performance(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = SizingLedger(Path(td) / "yolo.sqlite")
            cfg = self._config()
            ledger.decision(account_value_usd=20_000.0, config=cfg)
            before = ledger.decision(account_value_usd=22_000.0, config=cfg)
            self.assertAlmostEqual(before.raw_multiplier, 1.1)

            # $10k deposit has posted: equity is now $32k. Issue units at the
            # pre-flow NAV so the performance index remains 1.10x.
            ledger.record_external_flow(amount_usd=10_000.0, current_account_value_usd=32_000.0)
            after = ledger.decision(account_value_usd=32_000.0, config=cfg)
            self.assertAlmostEqual(after.raw_multiplier, 1.1, places=10)
            self.assertAlmostEqual(after.effective_nominal_usd, 55_000.0, places=6)

            # A subsequent 10% gain on the larger capital base should move the
            # unitized performance index from 1.10 to 1.21.
            later = ledger.decision(account_value_usd=35_200.0, config=cfg)
            self.assertAlmostEqual(later.raw_multiplier, 1.21, places=10)
            self.assertAlmostEqual(later.effective_nominal_usd, 60_500.0, places=6)

    def test_external_withdrawal_does_not_create_loss(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = SizingLedger(Path(td) / "yolo.sqlite")
            cfg = self._config()
            ledger.decision(account_value_usd=20_000.0, config=cfg)
            ledger.decision(account_value_usd=22_000.0, config=cfg)
            ledger.record_external_flow(amount_usd=-5_000.0, current_account_value_usd=17_000.0)
            after = ledger.decision(account_value_usd=17_000.0, config=cfg)
            self.assertAlmostEqual(after.raw_multiplier, 1.1, places=10)

    def test_multiplier_is_clipped(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = SizingLedger(Path(td) / "yolo.sqlite")
            cfg = self._config(min_mult=0.5, max_mult=1.5)
            ledger.decision(account_value_usd=20_000.0, config=cfg)
            high = ledger.decision(account_value_usd=40_000.0, config=cfg)
            self.assertTrue(high.clipped)
            self.assertAlmostEqual(high.raw_multiplier, 2.0)
            self.assertAlmostEqual(high.applied_multiplier, 1.5)
            self.assertAlmostEqual(high.effective_nominal_usd, 75_000.0)

            low = ledger.decision(account_value_usd=5_000.0, config=cfg)
            self.assertTrue(low.clipped)
            self.assertAlmostEqual(low.raw_multiplier, 0.25)
            self.assertAlmostEqual(low.applied_multiplier, 0.5)
            self.assertAlmostEqual(low.effective_nominal_usd, 25_000.0)

    def test_base_nominal_change_requires_explicit_rebase(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = SizingLedger(Path(td) / "yolo.sqlite")
            ledger.decision(account_value_usd=20_000.0, config=self._config(nominal=50_000.0))
            with self.assertRaises(SizingError):
                ledger.decision(account_value_usd=20_000.0, config=self._config(nominal=60_000.0))

    def test_compound_requires_dedicated_subaccount_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = SizingLedger(Path(td) / "yolo.sqlite")
            cfg = self._config(subaccount="")
            with self.assertRaises(SizingError):
                ledger.decision(account_value_usd=20_000.0, config=cfg)

    def test_fixed_mode_ignores_account_pnl(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = SizingLedger(Path(td) / "yolo.sqlite")
            cfg = YoloConfig(nominal_usd=50_000.0, sizing_mode="fixed")
            decision = ledger.decision(account_value_usd=99_000.0, config=cfg)
            self.assertEqual(decision.mode, "fixed")
            self.assertAlmostEqual(decision.effective_nominal_usd, 50_000.0)


if __name__ == "__main__":
    unittest.main()
