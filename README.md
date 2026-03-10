# azcoin-node-api (FastAPI)

Production-ready skeleton for **v0.1**.

This repo contains API scaffolding (settings, logging, routing, auth stub, tests, and Docker) plus JSON-RPC client wiring for AZCoin/Bitcoin nodes. It does **not** implement wallet/account business logic, money movement policies, or a database.

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
- **BTC_RPC_URL**: Bitcoin JSON-RPC URL (example: `http://127.0.0.1:8332`)
- **BTC_RPC_COOKIE_FILE**: Path to Bitcoin RPC cookie file (preferred; used when same-stack with bitcoind)
- **BTC_RPC_USER** / **BTC_RPC_PASSWORD**: Fallback for remote or non-shared-filesystem deployments
- **BTC_RPC_TIMEOUT_SECONDS**: RPC timeout seconds (default: `5`)
- **AZ_RPC_PORT**: RPC port used by docker compose (default: `19332`)
- **AZCOIN_CORE_IMAGE**: core docker image used by compose (default: `ghcr.io/satoshiware/azcoin-node:latest`)
- **BTC_RPC_PORT**: Bitcoin RPC port used by docker compose (default: `8332`)
- **BITCOIN_CORE_IMAGE**: bitcoin core docker image used by compose (default: `bitcoin/bitcoin-core:28.0`)
- **AZ_SHARE_DB_PATH**: sqlite share ledger path (default: `/data/shares.db`)
- **AZ_NODE_API_TOKEN**: optional Bearer token used by mining ingest/worker endpoints (default: empty)

Protected routes (currently `/v1/az/*`, `/v1/btc/*`, `/v1/tx/*`) require:

```
Authorization: Bearer <token>
```

Fail-closed rules:
- If `APP_ENV=prod` then `AUTH_MODE` must be `jwt` (the app will refuse to start otherwise).
- If `AUTH_MODE=dev_token` then `AZ_API_DEV_TOKEN` must be set (the app will refuse to start otherwise).

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

## API endpoints (v0.1)

- **GET** `/v1/health` (no auth)
- **GET** `/v1/az/node/info` (protected; calls AZCoin JSON-RPC and returns normalized info)
- **GET** `/v1/az/node/peers` (protected; calls AZCoin `getpeerinfo` and returns normalized peer list)
- **GET** `/v1/az/mempool/info` (protected; calls AZCoin `getmempoolinfo` and returns normalized mempool stats)
- **GET** `/v1/az/wallet/summary` (protected; calls AZCoin wallet RPC and returns normalized balances summary)
- **GET** `/v1/az/wallet/transactions?limit=50&since=<blockhash>` (protected; `since` is optional and must be a 64-hex blockhash used with `listsinceblock`)
- **GET** `/v1/btc/node/info` (protected; calls Bitcoin JSON-RPC and returns normalized info)
- **POST** `/v1/tx/send` (protected; calls Bitcoin `sendrawtransaction`)
- **POST** `/v1/mining/share` (token-protected when `AZ_NODE_API_TOKEN` is set; records share events in sqlite)
- **GET** `/v1/mining/workers` (requires `AZ_NODE_API_TOKEN` Bearer token when enabled)
- **GET** `/v1/mining/workers/{name}` (requires `AZ_NODE_API_TOKEN` Bearer token when enabled)

For `/v1/az/wallet/transactions` with `since`:
- Invalid `since` format returns `422` with `AZ_INVALID_SINCE`.
- Unknown/not-in-chain blockhash returns `404` with `AZ_SINCE_NOT_FOUND`.

For `/v1/az/wallet/transactions` results:
- Transactions are returned newest-first (descending by `time`).
- `limit` is applied after normalization and sorting.

For AZCoin protected endpoints:
- The API expects AZCoin RPC to run on chain `main` by default (override with `AZ_EXPECTED_CHAIN`).
- Chain mismatch returns `503` with `AZ_WRONG_CHAIN`.

Mining share ingest example:

```bash
curl -X POST "http://127.0.0.1:8080/v1/mining/share" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer testtoken-123" \
  -d '{
    "ts":1700000000,
    "ts_ms":1700000000123,
    "remote":"127.0.0.1",
    "worker":"BenC",
    "job_id":"job-42",
    "difficulty":1,
    "accepted":true,
    "reason":null,
    "extranonce2":"0a0b0c0d",
    "ntime":"65a1bc2f",
    "nonce":"deadbeef",
    "version_bits":"20000000",
    "accepted_unvalidated":true
  }'
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
