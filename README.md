# azcoin-node-api (FastAPI)

Production-ready skeleton for **v0.1**.

This repo intentionally contains **only** the API scaffolding (settings, logging, routing, auth stub, tests, and Docker). It does **not** implement real AZCoin RPC calls, money movement, or a database.

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
- **BTC_RPC_URL**: Bitcoin JSON-RPC URL (example: `http://127.0.0.1:8332`)
- **BTC_RPC_USER**: Bitcoin JSON-RPC username
- **BTC_RPC_PASSWORD**: Bitcoin JSON-RPC password
- **BTC_RPC_TIMEOUT_SECONDS**: RPC timeout seconds (default: `5`)
- **AZ_RPC_PORT**: RPC port used by docker compose (default: `19332`)
- **AZCOIN_CORE_IMAGE**: core docker image used by compose (default: `ghcr.io/satoshiware/azcoin-node:latest`)
- **BTC_RPC_PORT**: Bitcoin RPC port used by docker compose (default: `8332`)
- **BITCOIN_CORE_IMAGE**: bitcoin core docker image used by compose (default: `bitcoin/bitcoin-core:28.0`)

Protected routes (currently `/v1/az/*`) require:

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
- `docker-compose.yml` also starts `bitcoin-core` and wires the API to it via `BTC_RPC_URL=http://bitcoin-core:${BTC_RPC_PORT}`.

## API endpoints (v0.1)

- **GET** `/v1/health` (no auth)
- **GET** `/v1/az/node/info` (protected; calls AZCoin JSON-RPC and returns normalized info)
- **GET** `/v1/btc/node/info` (protected; calls Bitcoin JSON-RPC and returns normalized info)

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
