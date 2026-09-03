from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _b(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class YoloConfig:
    nominal_usd: float = 50_000.0
    sizing_mode: str = "fixed"
    min_nominal_multiplier: float = 0.25
    max_nominal_multiplier: float = 3.0
    require_dedicated_subaccount_for_compound: bool = True
    account_collateral_usd: float = 20_000.0
    margin_required: float = 0.10
    momentum_multiplier: float = 1.0
    trend_multiplier: float = 1.0
    carry_multiplier: float = 1.0
    trade_buffer: float = 0.02
    buffer_mode: str = "edge"
    max_asset_weight: float = 0.25
    max_gross_weight: float = 1.0
    max_margin_utilization: float = 0.60
    expected_universe_size: int = 10
    close_non_universe_positions: bool = True
    request_timeout_seconds: float = 15.0
    data_dir: str = "state"
    rw_api_key: str = ""
    rw_base_url: str = "https://api.robotwealth.com/v1"
    hl_account_address: str = ""
    hl_subaccount_address: str = ""
    hyperliquid_testnet: bool = True
    dry_run: bool = True

    @property
    def sqlite_path(self) -> Path:
        return Path(self.data_dir) / "yolo.sqlite"

    @property
    def hyperliquid_api_url(self) -> str:
        override = os.getenv("HL_API_URL", "").strip()
        if override:
            return override.rstrip("/")
        return (
            "https://api.hyperliquid-testnet.xyz"
            if self.hyperliquid_testnet
            else "https://api.hyperliquid.xyz"
        )

    @property
    def hyperliquid_user_address(self) -> str:
        return self.hl_subaccount_address or self.hl_account_address

    @classmethod
    def from_env(cls) -> "YoloConfig":
        return cls(
            nominal_usd=_f("YOLO_NOMINAL_USD", 50_000),
            sizing_mode=os.getenv("YOLO_SIZING_MODE", "fixed"),
            min_nominal_multiplier=_f("YOLO_MIN_NOMINAL_MULTIPLIER", 0.25),
            max_nominal_multiplier=_f("YOLO_MAX_NOMINAL_MULTIPLIER", 3.0),
            require_dedicated_subaccount_for_compound=_b("YOLO_REQUIRE_DEDICATED_SUBACCOUNT_FOR_COMPOUND", True),
            account_collateral_usd=_f("YOLO_ACCOUNT_COLLATERAL_USD", 20_000),
            margin_required=_f("YOLO_MARGIN_REQUIRED", 0.10),
            momentum_multiplier=_f("YOLO_MOMENTUM_MULTIPLIER", 1.0),
            trend_multiplier=_f("YOLO_TREND_MULTIPLIER", 1.0),
            carry_multiplier=_f("YOLO_CARRY_MULTIPLIER", 1.0),
            trade_buffer=_f("YOLO_TRADE_BUFFER", 0.02),
            buffer_mode=os.getenv("YOLO_BUFFER_MODE", "edge"),
            max_asset_weight=_f("YOLO_MAX_ASSET_WEIGHT", 0.25),
            max_gross_weight=_f("YOLO_MAX_GROSS_WEIGHT", 1.0),
            max_margin_utilization=_f("YOLO_MAX_MARGIN_UTILIZATION", 0.60),
            expected_universe_size=_i("YOLO_EXPECTED_UNIVERSE_SIZE", 10),
            close_non_universe_positions=_b("YOLO_CLOSE_NON_UNIVERSE_POSITIONS", True),
            request_timeout_seconds=_f("YOLO_REQUEST_TIMEOUT_SECONDS", 15.0),
            data_dir=os.getenv("YOLO_DATA_DIR", "state"),
            rw_api_key=os.getenv("RW_API_KEY", ""),
            rw_base_url=os.getenv("RW_BASE_URL", "https://api.robotwealth.com/v1"),
            hl_account_address=os.getenv("HL_ACCOUNT_ADDRESS", ""),
            hl_subaccount_address=os.getenv("HL_YOLO_SUBACCOUNT_ADDRESS", ""),
            hyperliquid_testnet=_b("HYPERLIQUID_TESTNET", True),
            dry_run=_b("DRY_RUN", True),
        )
