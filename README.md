# azcoin-api
python based azcoin core Fast-API Based REST service

# AZCoin REST API

This service provides a small REST API that proxies authenticated RPC calls to an AZCoin Core node. It is intended to run alongside the core node so that frontend clients can access node and network data with role-based access.

## Features

- **Reader access** for network dashboards (peers, mining info, node status).
- **Owner access** for node administration (wallet creation, RPC passthrough).
- Simple **API key** authentication using the `X-API-Key` header.

## Environment variables

Copy `.env.example` and adjust as needed:

- `AZCOIN_RPC_URL`: RPC URL for azcoind.
- `AZCOIN_RPC_USER`: RPC user.
- `AZCOIN_RPC_PASSWORD`: RPC password.
- `RPC_TIMEOUT`: request timeout in seconds (default: 10).
- `API_KEYS`: comma-separated `role:key` pairs (roles: `owner`, `reader`).

Example:

```
API_KEYS=owner:local-owner-key,reader:local-reader-key
```

## Run locally

```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Build container image

```bash
docker build -t azcoin-rest-api:local .
```

## API endpoints

- `GET /health` — liveness check.
- `GET /node/info` — blockchain + network info (reader).
- `GET /node/peers` — peers list (reader).
- `GET /network/summary` — peer count + mining info (reader).
- `POST /wallets?name=<wallet>` — create a wallet (owner).
- `POST /rpc/{method}` — owner-only RPC passthrough with JSON body `{"params": [...]}`.

### Notes on miners

AZCoin Core exposes mining statistics through `getmininginfo`. The REST API returns that payload verbatim for dashboards because the RPC does not provide a direct count of active miners. If the node later adds a direct miners count RPC, you can extend `/network/summary` to surface it.
