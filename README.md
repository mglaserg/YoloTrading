# Crypto YOLO Trading

Python implementation of the Robot Wealth-style Crypto YOLO portfolio: **Momentum + Trend + Carry**, inverse-volatility scaled with Robot Wealth-supplied `ewvol`, buffer-aware, compounding-capable, and staged for Hyperliquid execution.

## v0.5: pre-live

Version 0.5 deliberately stops one line before signed order transmission. It can now run the production decision path using real Robot Wealth and Hyperliquid data, persist the full decision trail, construct the exact ALO/post-only orders it **would** submit, and verify that the system is healthy enough for the final live-execution patch.

There is no private-key handling and no `/exchange` order submission in this version. Setting `YOLO_EXECUTION_MODE=execute` is a hard error.

### Current pipeline

```text
RW yolo/weights + yolo/volatilities
        ↓
immutable raw signal archive
        ↓
staleness / completeness gate
        ↓
Hyperliquid account + market state
        ↓
Hyperliquid cash-flow ledger sync
        ↓
unitized NAV / compound nominal
        ↓
1/3 Momentum + 1/3 Trend + 1/3 Carry
        ↓
RW ewvol inverse-vol scaling
        ↓
±25% asset cap / ≤100% gross cap
        ↓
2% relative trade-to-edge buffer
        ↓
risk and margin gate
        ↓
Hyperliquid BBO
        ↓
deterministic ALO order intents
        ↓
persistent pre-live decision ledger
        ↓
YOLO health summary
        ↓
STOP — no signed order transmission
```

## Production portfolio logic

For each asset:

1. `raw_weight = (momentum*M + trend*T + carry*C) / 3`
2. `vol_scaled = raw_weight / RW_ewvol`
3. clip each asset to `+/- YOLO_MAX_ASSET_WEIGHT` (default 25%)
4. if gross exceeds `YOLO_MAX_GROSS_WEIGHT` (default 100%), proportionally scale the whole portfolio down
5. multiply by the effective nominal allocation

Inverse volatility is the production baseline. There is no covariance/ERC estimator in the live path.

## Robot Wealth data

The client consumes:

- `yolo/weights`
- `yolo/volatilities`

Fields include `ticker`, `arrival_price`, `date`, the three megafactors, `combo_weight`, and supplied `ewvol`.

Every response is stored in `state/yolo.sqlite` **before validation**, including the raw body and SHA-256 hash. Stale or malformed responses remain available for diagnosis but are rejected for trading.

The signal guard blocks on stale dates, a wrong-size universe, duplicate/missing tickers, mismatched weights/volatility universes or dates, non-positive price/volatility, non-finite factors, and non-200 responses.

## Compound sizing

Two modes are supported:

```text
YOLO_SIZING_MODE=fixed
YOLO_SIZING_MODE=compound
```

In compound mode:

```text
effective nominal = base nominal × unitized YOLO NAV performance
```

A dedicated `HL_YOLO_SUBACCOUNT_ADDRESS` is required by default so unrelated strategies cannot contaminate YOLO NAV.

### Automatic cash-flow accounting

v0.5 reads Hyperliquid `userNonFundingLedgerUpdates`. Recognized deposits, withdrawals, subaccount transfers, internal transfers, and perp account-class transfers are treated as external strategy cash flows rather than P&L.

To avoid making deposits look like performance, YOLO also reads Hyperliquid `portfolio` history and uses the latest **perp account-value observation before each transfer** to issue/redeem strategy units at the contemporaneous NAV. If it cannot establish a pre-flow account value, it refuses to approximate and blocks the run.

Ambiguous/non-YOLO ledger events are persisted as `manual_review` and fail closed. Inspect them with:

```powershell
python -m crypto_yolo.cli --cashflow-status
```

If an event is understood and you intentionally want to establish a clean new baseline, use:

```powershell
python -m crypto_yolo.cli --rebase-compounding
```

A rebase resets NAV-per-unit to 1.0 at current YOLO equity, resets the cash-flow cursor, and acknowledges previously reviewed ambiguous events. `--record-flow` remains only as an emergency/admin fallback.

## Hyperliquid pre-live staging

The read-only client currently uses Hyperliquid `/info` for:

- `clearinghouseState`
- `metaAndAssetCtxs`
- `l2Book`
- `userNonFundingLedgerUpdates`
- `portfolio`
- `orderStatus` by client order ID (reconciliation scaffold)

For every trade above `YOLO_MIN_ORDER_USD`, v0.5 creates an ALO intent:

- BUY → current best bid
- SELL → current best ask
- `tif = Alo`
- universe exits are marked `reduce_only`
- quantity uses Hyperliquid `szDecimals`
- a deterministic `cloid` is generated for future idempotent submission
- proposed TCA versus RW `arrival_price` is stored

Because the BBO prices come from Hyperliquid itself, the preview uses currently valid exchange price levels rather than inventing tick-size rounding rules.

## Idempotency / restart preparation

`rebalance_runs` and `trade_intents` are persisted to SQLite. Identical pre-live runs deduplicate by a deterministic run key.

A separate `execution_locks` table is already present for the final live patch. Planning does **not** acquire the lock; next week's execution layer will reserve one execution per signal date/network/account before transmitting anything.

The schema also reserves transmitted/order-status fields, and `reconcile_orders.py` can query transmitted intents by `cloid` once live submission exists.

## Health summary

Every real pre-live run prints a concise system check covering:

- current RW signal
- signal archive
- Hyperliquid account state
- cash-flow ledger
- fixed/compound sizing
- risk gate
- ALO order construction
- execution-lock state
- live-execution interlock

Example shape:

```text
YOLO HEALTH
overall: PRE-LIVE READY
[OK  ] RW signals             current for 2026-09-03
[OK  ] Signal archive         snapshot 42 persisted
[OK  ] Hyperliquid state      MAINNET; equity $20,000.00
[OK  ] Cash-flow ledger       no new external cash flows
[OK  ] Sizing                 compound; effective nominal $52,300.00
[OK  ] Risk gate              approved
[OK  ] ALO construction       6 would-submit intent(s)
[OK  ] Idempotency            no execution lock for this signal date
[OK  ] Execution interlock    order transmission is disabled in v0.5
```

You can inspect the latest persisted run later with:

```powershell
python -m crypto_yolo.cli --health-status
```

A richer Streamlit operations/dashboard summary can come after the execution path is proven.

## Deployment controls

Use explicit modes rather than relying on double-negative booleans:

```text
YOLO_NETWORK=testnet
YOLO_EXECUTION_MODE=plan
```

or for real account **read-only planning**:

```text
YOLO_NETWORK=mainnet
YOLO_EXECUTION_MODE=plan
```

v0.5 intentionally rejects:

```text
YOLO_EXECUTION_MODE=execute
```

The old `DRY_RUN` and `HYPERLIQUID_TESTNET` settings remain as compatibility fallbacks, but the new settings take precedence.

## Setup on Windows

This project uses a packaged `src/` layout.

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
```

Or with uv while continuing to use your system interpreter:

```powershell
uv pip install --system -e .
python -m unittest discover -s tests -v
```

Copy configuration:

```powershell
Copy-Item .env.example .env
```

For compound mainnet planning, the important fields are:

```text
RW_API_KEY=...
HL_ACCOUNT_ADDRESS=0x...
HL_YOLO_SUBACCOUNT_ADDRESS=0x...

YOLO_NETWORK=mainnet
YOLO_EXECUTION_MODE=plan
YOLO_SIZING_MODE=compound
YOLO_NOMINAL_USD=50000
YOLO_TRADE_BUFFER=0.02
```

No private key belongs in v0.5.

## Commands

Fixture calculation:

```powershell
python -m crypto_yolo.cli --fixture examples/sample_snapshot.json
```

Full real pre-live run:

```powershell
python -m crypto_yolo.cli --live-data
```

Archive:

```powershell
python -m crypto_yolo.cli --archive-status
```

Health:

```powershell
python -m crypto_yolo.cli --health-status
```

Cash-flow audit:

```powershell
python -m crypto_yolo.cli --cashflow-status
```

Compounding state:

```powershell
python -m crypto_yolo.cli --sizing-status
```

## Final live-execution patch

Once several real mainnet `plan` runs are clean, the remaining work is intentionally narrow:

1. add the official Hyperliquid Python SDK / API-wallet signer
2. reserve the daily execution lock before transmission
3. submit the already-persisted ALO intents using their deterministic `cloid`s
4. reconcile resting/partial/filled/cancelled state by `cloid`
5. cancel/reprice ALO attempts under a bounded policy
6. persist fills/fees and run post-trade buffer verification
7. compute realized TCA vs RW `arrival_price`
8. mark the signal snapshot as actually rebalanced only after reconciliation succeeds

The alpha, inverse-vol sizing, compounding, signal archive, risk gate, and order-intent construction should not need to change for live launch.

## Linux deployment (recommended production target)

YOLO v0.5.2 is designed to run on Ubuntu/Lubuntu **without installing the project into the system Python and without creating a virtual environment**. Ubuntu may mark `/usr/bin/python3` as externally managed; YOLO avoids that issue by running directly from the repository `src/` tree.

The repository includes a launcher that sets `PYTHONPATH` automatically:

```bash
./bin/yolo --health-status
./bin/yolo --wait-for-signal
```

You do not need `pip install -e .`, `uv pip install --system`, `uv sync`, or `--break-system-packages` for this deployment path. Do **not** disable Ubuntu's externally-managed Python protection just to run YOLO.

The timer is UTC-native:

```text
09:01:00 UTC  systemd starts YOLO
              ↓
pull + archive RW weights/volatilities
              ↓
current signal?
  no  → retry every YOLO_SIGNAL_POLL_SECONDS
  yes → build exactly one pre-live plan and exit
              ↓
stop after YOLO_SIGNAL_WAIT_MINUTES if RW never becomes current
```

Every stale/rejected RW response still goes through the immutable signal archive. Malformed current payloads fail immediately rather than being retried as though they were merely late. Defaults:

```text
YOLO_SIGNAL_POLL_SECONDS=30
YOLO_SIGNAL_WAIT_MINUTES=15
```

### First Linux smoke test

From the repository root:

```bash
chmod +x bin/yolo
./bin/yolo --health-status
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The first command proves that the application can import directly from `src/` using the host's normal `python3`.

### User-level systemd timer

YOLO is intended to be one service on a general trading computer, not the only application on the host. The repository therefore installs a **user-level** `systemd` timer. No dedicated Linux account and no `/opt/yolotrading` layout are required.

The repository includes:

```text
deploy/systemd/yolo-daily.service
deploy/systemd/yolo-daily.timer
deploy/install-user-systemd.sh
deploy/uninstall-user-systemd.sh
```

Clone the repository anywhere under your normal Linux account, for example:

```text
~/trading/YoloTrading
```

Create `.env` in the repository root and protect it:

```bash
chmod 600 .env
```

Install the timer **as your normal user, without sudo**:

```bash
cd ~/trading/YoloTrading
bash deploy/install-user-systemd.sh
```

The installer writes units to `~/.config/systemd/user/`, points them at the current repository path, uses the current `python3`, and stores runtime SQLite/audit state under:

```text
~/.local/share/yolotrading
```

To allow the user timer to run while you are logged out, enable lingering once:

```bash
sudo loginctl enable-linger $USER
```

Check the schedule:

```bash
systemctl --user list-timers yolo-daily.timer
systemctl --user status yolo-daily.timer
```

Run the scheduled workflow immediately for testing:

```bash
systemctl --user start yolo-daily.service
journalctl --user -u yolo-daily.service -n 200 --no-pager
```

Watch a run live:

```bash
journalctl --user -u yolo-daily.service -f
```

Remove the timer without deleting YOLO's runtime history:

```bash
bash deploy/uninstall-user-systemd.sh
```

The timer uses `OnCalendar=*-*-* 09:01:00 UTC` and `Persistent=false`. If the computer is powered off at 09:01 UTC, YOLO does **not** automatically perform a late catch-up run after boot. That is intentional for eventual live trading.
