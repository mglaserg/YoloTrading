# Crypto YOLO Trading

Crypto YOLO is a daily **Momentum + Trend + Carry** crypto portfolio implemented against Robot Wealth signals and Hyperliquid execution. The production portfolio uses Robot Wealth-supplied `ewvol`, inverse-volatility sizing, a 2% trade buffer, compounding-aware nominal sizing, persistent point-in-time signal archives, and fail-closed execution controls.

## v0.6.1: low-volume dead-man compatibility

v0.6.1 keeps the v0.6 live-execution architecture and adds a narrow compatibility path for Hyperliquid accounts that are not yet eligible for `scheduleCancel`. The dead-man remains required by default; supervised canary runs may explicitly waive only Hyperliquid's specific "enough volume traded" rejection. All unrelated dead-man errors remain fail-closed.

It also releases the execution reservation if dead-man arming fails before an execution session starts, and can safely resume a v0.6 run stranded at that exact pre-transmission point.

## v0.6: guarded live execution

v0.6 crosses the final execution boundary while keeping the safe defaults unchanged:

```text
YOLO_EXECUTION_MODE=plan
YOLO_LIVE_TRADING_ENABLED=false
```

No signed order can be sent unless **both** are deliberately changed and an authorized Hyperliquid API/agent wallet is configured.

The live path now provides:

- official Hyperliquid Python SDK signing through a dedicated API/agent wallet
- explicit `long_short` and `long_only` direction modes
- deterministic 128-bit CLOIDs and one execution lock per signal-date/network/account
- maker-only ALO/post-only orders at the Hyperliquid BBO
- fresh BBO retrieval for every newly prepared live attempt; preview quotes are never reused as live quotes
- bounded cancel/reprice attempts; no taker fallback
- automatic `reduce_only` for position reductions
- two-stage reduce-only-close → open handling for sign flips
- Hyperliquid scheduled-cancel dead-man protection
- restart/resume reconciliation by CLOID
- exchange-confirmed `origSz - sz` fill checkpoints before any reissue
- `userFillsByTime` persistence for fill price, fees, closed P&L, and TCA
- live-position drift checks immediately before every signed order
- same-UTC-date enforcement for execution/resume
- post-trade position verification before a signal is marked rebalanced

## Portfolio logic

For each asset:

1. `raw_weight = (momentum*M + trend*T + carry*C) / 3`
2. `vol_scaled = raw_weight / RW_ewvol`
3. clip each asset to `+/- YOLO_MAX_ASSET_WEIGHT` (default 25%)
4. if gross exceeds `YOLO_MAX_GROSS_WEIGHT` (default 100%), scale the portfolio proportionally down
5. multiply final weight by the effective nominal allocation

Inverse volatility is the production baseline. There is no covariance/ERC estimator in the live path.

### Direction mode

The Robot Wealth/reference strategy remains long/short:

```text
YOLO_DIRECTION_MODE=long_short
```

An operational long-only variant is available:

```text
YOLO_DIRECTION_MODE=long_only
```

Long-only simply sets negative post-inverse-vol weights to zero. It **does not** rescale the remaining longs back to 100%; unused exposure remains USDC/cash. YOLO never silently falls back from long/short to long-only after a short rejection.

## Signal and decision pipeline

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
Momentum + Trend + Carry ensemble
        ↓
RW ewvol inverse-vol sizing
        ↓
asset / gross caps
        ↓
2% relative trade-to-edge buffer
        ↓
risk + projected margin gate
        ↓
Hyperliquid BBO
        ↓
durable ALO intents + deterministic CLOIDs
        ↓
PLAN mode: stop here
        ↓
EXECUTE mode + live flag + authorized API wallet
        ↓
dead-man switch → signed ALO orders
        ↓
CLOID/status/fill reconciliation
        ↓
bounded cancel/reprice
        ↓
post-trade position verification + TCA
        ↓
mark signal actually rebalanced
```

## Robot Wealth data and archive

YOLO consumes:

- `yolo/weights`
- `yolo/volatilities`

Fields include `ticker`, `arrival_price`, `date`, Momentum/Trend/Carry megafactors, `combo_weight`, and supplied `ewvol`.

Every response is persisted to `state/yolo.sqlite` **before validation**, including the raw body and SHA-256 hash. Stale or malformed responses remain available for diagnosis but cannot trade.

The signal guard blocks stale dates, wrong-size universes, duplicates, missing tickers, mismatched weights/volatility universes or dates, non-positive price/volatility, non-finite factors, and non-200 responses.

## Hyperliquid account handling

YOLO auto-detects Hyperliquid account abstraction mode.

- **Unified Account:** `spotClearinghouseState` USDC total is the trading-equity source; perp state remains the source for positions and margin.
- **Standard/disabled:** perp `clearinghouseState.marginSummary.accountValue` remains authoritative.
- **Portfolio Margin:** currently fails closed rather than approximating multi-asset equity.

Inspect this independently with:

```bash
./bin/yolo --account-status
```

## Compound sizing

Two sizing modes are supported:

```text
YOLO_SIZING_MODE=fixed
YOLO_SIZING_MODE=compound
```

Compound mode uses a unitized NAV ledger:

```text
effective nominal = base nominal × YOLO NAV-per-unit performance
```

Deposits and withdrawals issue/redeem units instead of creating fake performance. Recognized Hyperliquid non-funding ledger flows are synced automatically; ambiguous flows fail closed.

A dedicated `HL_YOLO_SUBACCOUNT_ADDRESS` is preferred and required by default for compound sizing. If the main Hyperliquid account is temporarily YOLO-only, that guard can be deliberately disabled in configuration.

Useful commands:

```bash
./bin/yolo --rebase-compounding
./bin/yolo --sizing-status
./bin/yolo --cashflow-status
```

## Live execution safety model

### Two-key interlock

Live transmission requires all of the following:

```text
YOLO_EXECUTION_MODE=execute
YOLO_LIVE_TRADING_ENABLED=true
HL_API_WALLET_PRIVATE_KEY=<authorized API/agent wallet private key>
```

The signing key must resolve through Hyperliquid `userRole` as an **agent** owned by `HL_ACCOUNT_ADDRESS`. A master-wallet private key is deliberately rejected by YOLO's readiness check.

The main public account remains `HL_ACCOUNT_ADDRESS`. If `HL_YOLO_SUBACCOUNT_ADDRESS` is configured, the SDK signs through the API wallet and routes the trade to that subaccount.

### Maker-only policy

Normal execution is ALO/post-only:

- BUY → current best bid
- SELL → current best ask
- bounded `YOLO_EXECUTION_MAX_REPRICES`
- wait `YOLO_EXECUTION_REPRICE_SECONDS` before cancel/reprice
- no IOC/taker fallback in v0.6

If the material remainder is still unfilled after the attempt budget, the run enters `attention`; it does not chase the market.

### Position reductions and sign flips

Any trade that only reduces an existing position is sent `reduce_only`.

If a target crosses through zero, YOLO creates two durable intents:

```text
short → long:  reduce-only BUY to zero → normal BUY to target
long  → short: reduce-only SELL to zero → normal SELL to target
```

The opening leg is not attempted if the closing leg remains materially unfilled.

### Duplicate-fill protection

Before any reprice or restart continuation, YOLO queries the existing CLOID with Hyperliquid `orderStatus`. The exchange-reported `origSz - sz` is treated as the authoritative quantity that may have filled. `userFillsByTime` must reconcile to that amount before another order can be sent.

If the exchange says an order filled but the public fill feed has not caught up, YOLO stops with an attention state and explicitly **does not reissue**. Resume later with the same run ID.

### Position-drift protection

Immediately before every signed order, YOLO re-reads Hyperliquid positions and compares the live quantity with:

```text
persisted initial position + fills reconciled by YOLO
```

A material mismatch means something outside the expected run changed the account. Execution fails closed and asks for a new plan rather than sending a stale size.

### Dead-man switch

Before live transmission YOLO schedules Hyperliquid's native cancel-all dead-man switch. A clean completion removes the schedule. If the process dies while an order rests, Hyperliquid can cancel remaining open orders after the configured deadline.

```text
YOLO_EXECUTION_DEADMAN_SECONDS=300
YOLO_DEADMAN_REQUIRED=true
```

For a **supervised first canary only**, a new/low-volume Hyperliquid account may return `Cannot set scheduled cancel time until enough volume traded`. In that specific case you may set:

```text
YOLO_DEADMAN_REQUIRED=false
```

YOLO still attempts to arm the dead-man. It proceeds without it only when Hyperliquid returns that exact volume-eligibility class of error, prints a prominent warning before transmitting, and continues to fail closed for authentication, network, parameter, or any other scheduled-cancel failure. Without the dead-man, a process crash can leave a resting ALO order live, so keep the systemd timer stopped and supervise the canary. Restore `true` as soon as Hyperliquid accepts the scheduled cancel.

### Restart/resume

Every intent and attempt is written to SQLite before network transmission. A crash between prepare and send reuses the same prepared CLOID; a previously transmitted CLOID is reconciled before anything new is sent.

An untransmitted same-day plan can be rebuilt safely: its preview BBO/intents are refreshed in place. During actual execution, every newly prepared attempt fetches a fresh BBO again immediately before order preparation, so an earlier preview quote is never treated as a live limit price.

Resume an attention run with:

```bash
./bin/yolo --resume-execution RUN_ID
```

A run from a prior UTC signal date cannot be resumed into live execution. `--resume-execution` normally refuses a run that never entered an execution session. The sole compatibility exception is a v0.6-style run that already holds its own execution reservation but failed before transmission while arming the dead-man; v0.6.1 may resume that same run, using fresh BBOs before any order.

## Lubuntu setup

YOLO is designed to run directly from the repository without a project virtual environment and without modifying Ubuntu's externally managed system Python.

The launcher uses `src/` directly:

```bash
./bin/yolo --health-status
./bin/yolo --account-status
./bin/yolo --live-data
```

### Install only the live signing dependencies

Plan/read-only mode remains dependency-light. Live signing uses Hyperliquid's official Python SDK, pinned into a repository-local `.vendor/` directory:

```bash
bash deploy/install-live-deps.sh
```

The installer uses `uv pip install --target`; it creates neither a virtual environment nor a system-Python installation.

`.vendor/` is ignored by Git.

### API wallet

Create/authorize a dedicated Hyperliquid API/agent wallet for the main account. Put only that agent private key in `.env`:

```text
HL_ACCOUNT_ADDRESS=0xYOUR_MAIN_ACCOUNT
HL_YOLO_SUBACCOUNT_ADDRESS=
HL_API_WALLET_PRIVATE_KEY=...
```

Never commit `.env` or the private key. Protect it on Lubuntu:

```bash
chmod 600 .env
```

Check the signer without sending an order:

```bash
./bin/yolo --execution-status
```

## Recommended first-live sequence

Keep the already-tested plan path while adding the signer:

```text
YOLO_NETWORK=mainnet
YOLO_EXECUTION_MODE=plan
YOLO_LIVE_TRADING_ENABLED=false
```

Then:

```bash
bash deploy/install-live-deps.sh
./bin/yolo --execution-status
./bin/yolo --live-data
```

For the first real-money canary, use a deliberately small `YOLO_NOMINAL_USD`. If short permissions/margin behavior are still uncertain, explicitly choose:

```text
YOLO_DIRECTION_MODE=long_only
```

Only when the preview is correct, deliberately set:

```text
YOLO_EXECUTION_MODE=execute
YOLO_LIVE_TRADING_ENABLED=true
```

Then a normal scheduled/manual `--wait-for-signal` or `--live-data` run is allowed to cross the signing boundary.

## User-level systemd timer

YOLO runs as a user-level service on the general Lubuntu trading host. The existing timer remains UTC-native at **09:01 UTC** and `Persistent=false`.

Install/refresh it from the repository root:

```bash
bash deploy/install-user-systemd.sh
sudo loginctl enable-linger $USER
systemctl --user status yolo-daily.timer
systemctl --user list-timers --all | grep yolo
```

Manual service test:

```bash
systemctl --user start yolo-daily.service
journalctl --user -u yolo-daily.service -n 200 --no-pager
```

The service runs:

```bash
./bin/yolo --wait-for-signal
```

Therefore the timer obeys the current `.env` execution mode. Keep `plan/false` until live transmission is intentionally enabled.

## Operations commands

```bash
# Account/equity only
./bin/yolo --account-status

# Current compounding state
./bin/yolo --sizing-status

# Signal archive
./bin/yolo --archive-status

# Latest run health
./bin/yolo --health-status

# Live signer + execution readiness, no order transmission
./bin/yolo --execution-status

# Build today's real-data run; sends only if execute + live flag are both enabled
./bin/yolo --live-data

# Poll around the daily publication time and run once
./bin/yolo --wait-for-signal

# Reconcile/resume an attention run
./bin/yolo --resume-execution RUN_ID
```

## Configuration highlights

```text
YOLO_NETWORK=mainnet
YOLO_EXECUTION_MODE=plan
YOLO_LIVE_TRADING_ENABLED=false
YOLO_DIRECTION_MODE=long_short

YOLO_NOMINAL_USD=50000
YOLO_SIZING_MODE=compound
YOLO_MIN_NOMINAL_MULTIPLIER=0.25
YOLO_MAX_NOMINAL_MULTIPLIER=3.00

YOLO_TRADE_BUFFER=0.02
YOLO_BUFFER_MODE=edge
YOLO_MAX_ASSET_WEIGHT=0.25
YOLO_MAX_GROSS_WEIGHT=1.0
YOLO_MAX_MARGIN_UTILIZATION=0.60
YOLO_MIN_ORDER_USD=10

YOLO_EXECUTION_MAX_REPRICES=2
YOLO_EXECUTION_REPRICE_SECONDS=5
YOLO_EXECUTION_DEADMAN_SECONDS=300
```

`YOLO_ACCOUNT_COLLATERAL_USD` is retained for fixture/offline risk calculations. Real live runs pass the actual Hyperliquid account equity into the risk gate.

## Tests

No install is required:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The live-execution tests use fakes only; the test suite never transmits an order.
