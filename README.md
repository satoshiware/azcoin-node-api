# azcoin-node-api (FastAPI)

Production-ready skeleton for **0.2.0**.

This repo contains API scaffolding (settings, logging, routing, auth stub, tests, and Docker) plus JSON-RPC client wiring for AZCoin/Bitcoin nodes. It does **not** implement wallet/account business logic or money movement policies. It now includes a narrow SQLite store for durable translator `blocks_found` counter-delta evidence only.

## Quickstart (local)

Prereqs: **Python 3.11+**

PowerShell:

```powershell
cd C:\AZCoin\01_PROJECTS\azcoin-node-api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt

# Run (dev)
$env:PYTHONPATH="src"
uvicorn node_api.main:app --reload --host 0.0.0.0 --port 8080
```

Open:
- Docs: `http://localhost:8080/docs`
- Health: `http://localhost:8080/v1/health`

## Environment variables

Copy `.env.example` to `.env`.

- **APP_ENV**: `dev|prod` (default: `dev`)
- **PORT**: API port (default: `8080`)
- **API_V1_PREFIX**: versioned API prefix (default: `/v1`)
- **LOG_LEVEL**: root log level (default: `INFO`)
- **AUTH_MODE**: `dev_token|jwt` (default: `dev_token` in dev, `jwt` in prod)
- **AZ_API_DEV_TOKEN**: required when `AUTH_MODE=dev_token` (no default)
- **AZ_RPC_URL**: AZCoin JSON-RPC URL (example: `http://127.0.0.1:19332`)
- **AZ_RPC_USER**: AZCoin JSON-RPC username
- **AZ_RPC_PASSWORD**: AZCoin JSON-RPC password
- **AZ_RPC_TIMEOUT_SECONDS**: RPC timeout seconds (default: `5`)
- **AZ_EXPECTED_CHAIN**: expected AZCoin chain name (default: `main`)
- **AZ_REWARD_OWNERSHIP_ADDRESSES**: comma-separated list of coinbase payout addresses owned by this node, used by `GET /v1/az/blocks/rewards` to classify blocks as owned. Whitespace is trimmed and empty entries are dropped; addresses match exactly. Required (along with `AZ_REWARD_OWNERSHIP_SCRIPT_PUBKEYS` or both) when calling `/v1/az/blocks/rewards?owned_only=true`.
- **AZ_REWARD_OWNERSHIP_SCRIPT_PUBKEYS**: comma-separated list of coinbase `scriptPubKey` hex strings owned by this node. Whitespace is trimmed, empty entries are dropped, and matching is case-insensitive.

  > ⚠️ `owned_only` and `AZ_REWARD_OWNERSHIP_*` filter blocks whose coinbase paid into a configured **pool wallet**. They do **not** identify which SC node produced the block, because all SC nodes currently pay into the same shared pool wallet. Per-SC-node attribution requires translator-side block-candidate logging and is not provided by `/v1/az/blocks/rewards`. See [`docs/api/ledger-mvp-endpoints.md`](docs/api/ledger-mvp-endpoints.md) for the full reward / ledger MVP contract and the planned rename to `AZ_REWARD_POOL_*` / `pool_only`.
- **BTC_RPC_URL**: Bitcoin JSON-RPC URL (example: `http://127.0.0.1:8332`)
- **BTC_RPC_COOKIE_FILE**: Path to Bitcoin RPC cookie file (preferred; used when same-stack with bitcoind)
- **BTC_RPC_USER** / **BTC_RPC_PASSWORD**: Fallback for remote or non-shared-filesystem deployments
- **BTC_RPC_TIMEOUT_SECONDS**: RPC timeout seconds (default: `5`)
- **AZ_RPC_PORT**: RPC port used by docker compose (default: `19332`)
- **AZCOIN_CORE_IMAGE**: core docker image used by compose (default: `ghcr.io/satoshiware/azcoin-node:latest`)
- **BTC_RPC_PORT**: Bitcoin RPC port used by docker compose (default: `8332`)
- **BITCOIN_CORE_IMAGE**: bitcoin core docker image used by compose (default: `bitcoin/bitcoin-core:28.0`)
- **TRANSLATOR_LOG_PATH**: optional path to the translator process log file for observability endpoints (unset disables translator log reads)
- **TRANSLATOR_LOG_DEFAULT_LINES**: default line count for `GET /v1/translator/logs/tail` when `lines` is omitted (default: `200`)
- **TRANSLATOR_LOG_MAX_LINES**: maximum lines read from the log tail per request and upper bound for the `lines` query param (default: `1000`)
- **TRANSLATOR_MONITORING_BASE_URL**: optional base URL of the translator's built-in monitoring HTTP server (for example `http://127.0.0.1:5000`). When unset, monitoring-backed routes return a stable `unconfigured` envelope instead of calling upstream.
- **TRANSLATOR_MONITORING_TIMEOUT_SECS**: HTTP timeout for allowlisted monitoring GETs (default: `3`)
- **TRANSLATOR_BLOCKS_FOUND_DB_PATH**: SQLite path for durable translator `blocks_found` counter-delta evidence and poller state (default: `.data/translator_blocks_found.sqlite3`)

Protected routes (currently `/v1/az/*`, `/v1/btc/*`, `/v1/node/*`, `/v1/tx/*`, `/v1/translator/*`) require:

```
Authorization: Bearer <token>
```

Fail-closed rules:
- If `APP_ENV=prod` then `AUTH_MODE` must be `jwt` (the app will refuse to start otherwise).
- If `AUTH_MODE=dev_token` then `AZ_API_DEV_TOKEN` must be set (the app will refuse to start otherwise).

## Bare-metal install (Ubuntu/Debian + systemd)

Prerequisites:
- Linux host with `systemd`
- Python 3.11+ available as `python3`
- Access to local AZCoin RPC and optional Bitcoin RPC

Run the installer from the repo root:

```bash
cd /path/to/azcoin-node-api
sudo bash deploy/linux/install.sh
```

The installer copies the app to `/opt/azcoin-node-api`, creates a virtualenv at `/opt/azcoin-node-api/.venv`, installs the systemd unit at `/etc/systemd/system/azcoin-node-api.service`, creates `/var/log/azcoin-node-api`, and seeds `/etc/azcoin-node-api/azcoin-node-api.env` if it does not already exist.

Edit the env file before first start:

```bash
sudoedit /etc/azcoin-node-api/azcoin-node-api.env
```

For this branch, translator log and monitoring routes stay disabled until you set `TRANSLATOR_LOG_PATH` and/or `TRANSLATOR_MONITORING_BASE_URL`. A common testing choice is a translator log under `/var/log/azcoin-node-api/`. Durable translator block-found evidence is separate and writes to `TRANSLATOR_BLOCKS_FOUND_DB_PATH`.

Service commands:

```bash
sudo systemctl start azcoin-node-api
sudo systemctl stop azcoin-node-api
sudo systemctl status azcoin-node-api
sudo journalctl -u azcoin-node-api -f
```

Basic verification:

```bash
curl http://127.0.0.1:8080/v1/health

curl \
  -H "Authorization: Bearer change-me" \
  http://127.0.0.1:8080/v1/az/node/info

curl \
  -H "Authorization: Bearer change-me" \
  http://127.0.0.1:8080/v1/translator/status
```

Replace `change-me` with the `AZ_API_DEV_TOKEN` value from `/etc/azcoin-node-api/azcoin-node-api.env`. If translator monitoring is configured, you can also verify a live route such as `/v1/translator/miner-work/snapshot` (preferred) or `/v1/translator/status`.

Translator block-found poller command:

```bash
cd /opt/azcoin-node-api
source .venv/bin/activate
export PYTHONPATH=src
python -m node_api.services.translator_blocks_found_poller --once
python -m node_api.services.translator_blocks_found_poller
```

## Running with Docker

```powershell
cd C:\AZCoin\01_PROJECTS\azcoin-node-api
docker compose up --build
```

Service name in compose: `azcoin-api`

Notes:
- `docker-compose.yml` starts `azcoin-core` on the external network `aznet` and wires the API to it via `AZ_RPC_URL=http://azcoin-core:${AZ_RPC_PORT}`.
- The core RPC port is **not** published to the host; it is only reachable inside `aznet`.
- `docker-compose.yml` also starts `bitcoin-core` and wires the API via `BTC_RPC_URL` and `BTC_RPC_COOKIE_FILE` (cookie auth; no manual password copying).

## API endpoints (0.2.0)

- **GET** `/v1/health` (no auth)
- **GET** `/v1/az/node/info` (protected; calls AZCoin JSON-RPC and returns normalized info)
- **GET** `/v1/az/node/peers` (protected; calls AZCoin `getpeerinfo` and returns normalized peer list)
- **GET** `/v1/az/mining/template/current` (protected; calls AZCoin `getblocktemplate` and returns minimal pool DTO: job_id, prev_hash, version, nbits, ntime, clean_jobs, height)
- **GET** `/v1/az/mining/status` (protected; returns RPC connectivity, chain/blocks/headers, and template fetch health)
- **GET** `/v1/az/mempool/info` (protected; calls AZCoin `getmempoolinfo` and returns normalized mempool stats)
- **GET** `/v1/az/wallet/summary` (protected; calls AZCoin wallet RPC and returns normalized balances summary)
- **GET** `/v1/az/wallet/transactions?limit=50&since=<blockhash>` (protected; `since` is optional and must be a 64-hex blockhash used with `listsinceblock`)
- **GET** `/v1/az/blocks/rewards` (protected; recent reward-block details with strict satoshi conversion, ownership classification, optional time-window filtering, and **direct blockhash lookup**. Two modes:
  - *scan mode (default)* — query: `limit` (1–200, default 50), `owned_only` (default `true`; configured pool/reward-wallet filter, see caveat), `start_time` / `end_time` / `time_field` for half-open interval filtering. `time_field=mediantime` remains the monotonic direct scan path; `time_field=time` is now bounded by a mediantime anchor and then filtered by header `time`, so narrow operator windows fail closed instead of hanging on a tip-to-genesis walk.
  - *blockhash-lookup mode* — repeated `?blockhash=<64hex>` and/or comma-separated `?blockhashes=<h1>,<h2>` activate direct lookup (max 500 unique hashes per request; ignores `limit`; bypasses the `owned_only` precheck because the caller is naming exact blocks). May be combined with `start_time` / `end_time` / `time_field`. Hashes that `getblock` resolves but that are **not** active-chain reward truth (e.g. `confirmations <= 0`, `is_on_main_chain: false`, as with stale/orphan blocks) are **excluded from `blocks[]`** and listed only in `stale_blockhashes` — they are not payable and not maturity-eligible. **Ledger code must ingest reward truth only from `blocks[]`**; treat `stale_blockhashes`, `unresolved_blockhashes`, and `filtered_out_blockhashes` as diagnostics.
  - Top-level metadata always emitted: `lookup_mode` (`"scan"` | `"blockhashes"`), `requested_blockhash_count`, `resolved_blockhash_count`, `unresolved_blockhashes`, `stale_blockhashes` (always `[]` in scan mode), `filtered_out_blockhashes`, `time_filter`. See [`docs/api/ledger-mvp-endpoints.md`](docs/api/ledger-mvp-endpoints.md) section 2.1.)
- **GET** `/v1/btc/node/info` (protected; calls Bitcoin JSON-RPC and returns normalized info)
- **POST** `/v1/tx/send` (protected; calls Bitcoin `sendrawtransaction`)
- **GET** `/v1/translator/status` (protected; merged health: log file panel plus optional live monitoring probe; overall `status` is `ok`, `degraded`, or `unconfigured`)
- **GET** `/v1/translator/summary` (protected; log-backed status plus level/category counts over the log tail; query: `lines` default `500`, max `2000`)
- **GET** `/v1/translator/miner-work/snapshot` (protected; ledger-ready normalized join of `/upstream/channels` and `/downstreams` keyed by `channel_id`; ledger-sensitive numerics like `share_work_sum` / `best_diff` / `hashrate` are returned as strings; fail-closed when either side is unreachable; see [`docs/api/ledger-mvp-endpoints.md`](docs/api/ledger-mvp-endpoints.md) section 5)
- **GET** `/v1/translator/blocks-found` (protected; durable API-side translator `blocks_found` counter-delta evidence persisted by the poller; newest-first history over `detected_time`; this does **not** prove chain reward maturity or execute payouts; ledger must still verify rewards through `/v1/az/blocks/rewards`; `channel_id` is metadata only and not payout identity)
- **GET** `/v1/translator/logs/tail` (protected; newest-first normalized records from the translator log tail; query: `lines`, optional `level`, `contains`)
- **GET** `/v1/translator/events/recent` (protected; newest-first normalized records; query: `limit`, optional `category`, `level`, `contains`)
- **GET** `/v1/translator/errors/recent` (protected; newest-first `WARN`/`ERROR` records; query: `limit`)

Deprecated routes (kept for diagnostics; OpenAPI marks them with `deprecated: true` — do not build new clients on top of these, prefer the canonical alternatives noted above):

- **GET** `/v1/translator/runtime` *(deprecated)* — live `GET .../api/v1/health` passthrough; use `/v1/translator/status` instead.
- **GET** `/v1/translator/global` *(deprecated)* — live `GET .../api/v1/global` passthrough; use `/v1/translator/status`.
- **GET** `/v1/translator/upstream` *(deprecated)* — live `GET .../api/v1/server` passthrough; use `/v1/translator/miner-work/snapshot`.
- **GET** `/v1/translator/upstream/channels` *(deprecated)* — live `GET .../api/v1/server/channels` passthrough; use `/v1/translator/miner-work/snapshot`.
- **GET** `/v1/translator/downstreams` *(deprecated)* — live `GET .../api/v1/sv1/clients` passthrough; use `/v1/translator/miner-work/snapshot`.
- **GET** `/v1/translator/downstreams/{client_id}` *(deprecated)* — live `GET .../api/v1/sv1/clients/{client_id}` passthrough; use `/v1/translator/miner-work/snapshot`.
- **GET** `/v1/events/recent-legacy` *(deprecated)* — pre-EventStore in-memory ZMQ buffer; the canonical recent-events endpoint is the EventStore-backed `GET /v1/events/recent`.

Log-backed translator routes reflect **historical** lines from `TRANSLATOR_LOG_PATH` (tail, incidents, aggregates). Monitoring-backed routes reflect **live** translator process state from `TRANSLATOR_MONITORING_BASE_URL` only on a fixed allowlist (no generic proxy). The API does not add config writes, restarts, Prometheus passthrough, or arbitrary upstream paths.

`GET /v1/translator/blocks-found` reads durable API-owned SQLite evidence produced by the translator block-found poller rather than re-scraping logs or querying `journalctl` on request. Each row proves only that a translator-side `blocks_found` counter increased for a stable miner identity at `detected_time`. It does not prove exact blockhash, chain inclusion, reward maturity, payout eligibility, wallet movement, or any payout decision unless separate direct evidence is correlated later.

For `/v1/az/wallet/transactions` with `since`:
- Invalid `since` format returns `422` with `AZ_INVALID_SINCE`.
- Unknown/not-in-chain blockhash returns `404` with `AZ_SINCE_NOT_FOUND`.

For `/v1/az/wallet/transactions` results:
- Transactions are returned newest-first (descending by `time`).
- `limit` is applied after normalization and sorting.

For AZCoin protected endpoints:
- The API expects AZCoin RPC to run on chain `main` by default (override with `AZ_EXPECTED_CHAIN`).
- Chain mismatch returns `503` with `AZ_WRONG_CHAIN`.

### Translator log examples (observability)

Plain text line (Rust-style `target: message` after ISO timestamp and level):

```text
2026-04-10T21:02:48.715038Z INFO translator_sv2::downstream: Downstream connection established
```

JSON line (one JSON object per line):

```json
{"ts":"2026-04-10T21:05:00.000000Z","level":"WARN","target":"translator_sv2::upstream","message":"Upstream disconnected: reset by peer"}
```

Example **`GET /v1/translator/status`** response (merged log + monitoring):

```json
{
  "status": "ok",
  "configured": true,
  "log_configured": true,
  "monitoring_configured": true,
  "log_status": "ok",
  "monitoring_status": "ok",
  "last_event_ts": "2026-04-10T21:02:48.715038Z",
  "recent_error_count": 3,
  "upstream_channels": 2,
  "downstream_clients": 5,
  "log_path": "/var/log/azcoin/translator.log"
}
```

Example **`GET /v1/translator/global`** monitoring envelope when upstream is healthy:

```json
{
  "status": "ok",
  "configured": true,
  "data": { "version": "1.0.0" },
  "detail": null
}
```

Example **`GET /v1/translator/summary?lines=500`** response:

```json
{
  "status": "ok",
  "configured": true,
  "log_path": "/var/log/azcoin/translator.log",
  "exists": true,
  "readable": true,
  "total_records_scanned": 387,
  "counts_by_level": {"INFO": 320, "WARN": 12, "ERROR": 3},
  "counts_by_category": {"downstream.connect": 20, "submit": 210, "difficulty.update": 5, "log": 152},
  "last_event_ts": "2026-04-10T21:02:48.715038Z",
  "recent_error_count": 15
}
```

## Developer notes

- Keep API versioning centralized via `API_V1_PREFIX`; routers should use resource-only prefixes (`/tx`, `/az`, `/btc`) and be mounted in `create_app()`.
- Protected route enforcement is path-boundary aware: `/v1/tx/*` is protected, while similarly named paths like `/v1/tx-extra` are not implicitly matched.
- `tx/send` maps RPC failures to stable HTTP responses: config issues (`503`), upstream transport/HTTP issues (`502`), and RPC validation/rejection errors (`400`).

## Tests

```powershell
cd C:\AZCoin\01_PROJECTS\azcoin-node-api
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH="src"
pytest -q
```

## Lint / format (ruff)

```powershell
cd C:\AZCoin\01_PROJECTS\azcoin-node-api
.\.venv\Scripts\Activate.ps1
ruff check .
ruff format .
```

## Pre-commit

```powershell
cd C:\AZCoin\01_PROJECTS\azcoin-node-api
.\.venv\Scripts\Activate.ps1
pre-commit install
pre-commit run -a
```
