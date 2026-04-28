# Ledger MVP — API Contract

**Status:** Partially implemented. Translator snapshot and translator
block-found evidence endpoints are live; ledger endpoints below remain
the proposal surface.
**Owner:** azcoin-node-api maintainers.
**Last updated:** 2026-04-28.
**Scope:** Defines which API surface owns which kind of truth for the
SC-node reward / ledger MVP, and lists the endpoint changes needed to
get there. This document is the single source of truth for the *shape*
of the upcoming ledger endpoints; implementation will follow in later
PRs that touch production code.

> **Reading order if you are new to this:** sections 1 → 2 → 3 first,
> then jump to 5/6 for the new endpoint specs. Section 4 explains why
> the existing `owned_only` parameter is being repositioned — read it
> before changing anything around `AZ_REWARD_OWNERSHIP_*`.

---

## 1. Architecture rule

The MVP separates three kinds of truth across three logical APIs.
Endpoints live in this single FastAPI service today, but each one must
respect exactly one of the three roles below. Mixing roles is what
created the misleading "owned_only" semantic that section 4 deprecates.

### 1.1 Chain API = reward truth

Source of truth: the AZCoin node, via JSON-RPC.

What it answers:

- *Did a block get mined? When? Is it on the active chain?*
- *What is the coinbase total in satoshis, exactly, with no
  floating-point drift?*
- *Is that coinbase mature (>=100 confirmations) yet?*

What it must **not** answer:

- *Which SC node mined this block?*
- *Which downstream worker mined this block?*
- *How should the reward be split?*

Endpoint family: `/v1/az/*` (today: `/v1/az/blocks/rewards`).

### 1.2 Translator API = local miner / work truth

Source of truth: the SRI translator process running on the same SC node,
via its read-only monitoring HTTP server.

What it answers:

- *Which downstream SV1 clients are connected to this translator?*
- *What is the authorized worker name on each connection?*
- *How much share work has each upstream channel accumulated?*
- *What is the current target / best diff per channel?*
- *How many block-difficulty shares ("blocks_found") has each channel
  produced?*

What it must **not** answer:

- *Did this share land in a chain block?* (would require candidate-event
  logging — see section 7.)
- *What is each worker owed in satoshis?* (that is ledger truth.)

Endpoint family: `/v1/translator/*` (today: `downstreams`,
`upstream/channels`, `miner-work/snapshot`, `blocks-found`, etc.).

### 1.3 Ledger API = accounting truth

Source of truth: this service's own database (to be added in a later
PR), populated by ingesting reward events from the chain API and miner
work counters from the translator API across discrete time intervals.

What it answers:

- *Between T0 and T1, what was each worker's share-work delta?*
- *Between T0 and T1, what reward events landed on the chain (with
  exact coinbase totals and maturity)?*
- *Given a proposed split rule, what would each worker's share be?*

What it must **not** answer (MVP non-goals — see section 8):

- *Move money to anyone.*
- *Sign or broadcast transactions.*
- *Maintain on-chain per-miner balances.*

Endpoint family: `/v1/ledger/*` (new — see section 6).

### 1.4 Shared pool wallet caveat

> **All SC nodes currently pay coinbase outputs into the same shared
> pool wallet.** This is the single most important constraint for this
> document.

Practical consequences:

- The coinbase address on a block does **not** identify which SC node
  produced the block. Two different SC nodes producing two different
  blocks both pay into the same address(es).
- Therefore any feature that says "this is *my* block because the
  coinbase paid *my* address" is wrong by construction in the current
  topology. It can at best say "this block paid into the pool wallet."
- Per-SC-node attribution can only come from data the SC node itself
  produces (translator candidate events + future block-candidate
  logging — see section 7), never from chain-side coinbase inspection.

This is why section 4 deprecates the `owned_only=true` semantic on
`/v1/az/blocks/rewards`: the parameter is currently labeled as
"ownership" but in practice it can only ever mean "paid into a
configured pool wallet."

---

## 2. Current endpoints

The three endpoints listed in this section are the ones already in
production today that this document re-classifies. Their behavior is
not changed by this document; only their **role** in the contract is
clarified.

### 2.1 `GET /v1/az/blocks/rewards`

- **Truth role:** chain reward truth (section 1.1).
- **Auth:** protected (`Authorization: Bearer <token>`).
- **What it does:** returns one normalized entry per accepted block,
  each with the strict integer coinbase-total in satoshis, per-output
  details, maturity fields, and ownership classification fields. The
  endpoint operates in two mutually exclusive modes:
  1. **Scan mode (default).** Walks the active AZCoin chain from tip
     downward, optionally filtered by a half-open time window. Driven
     by `limit` *or* `start_time` / `end_time` / `time_field`.
  2. **Blockhash-lookup mode.** Resolves a caller-supplied set of
     block hashes directly via `getblock(hash, 2)` — no height walk,
     `limit` ignored. This is the path the ledger uses to verify
     translator-provided block-found hashes without scanning large
     time windows.
- **Source:** AZCoin RPC `getblockchaininfo` (always; for tip metadata
  + chain validation), `getblockhash` (scan modes only), `getblock`
  (every mode).
- **Key query params (scan mode):** `limit` (1–200, default 50),
  `owned_only` (default `true` — see section 4 for the naming
  caveat), `start_time`, `end_time`, `time_field` (`"time"` |
  `"mediantime"`).
- **Key query params (blockhash-lookup mode):**
  - `blockhash` (repeatable, e.g. `?blockhash=<h1>&blockhash=<h2>`).
    Each value must be exactly 64 hexadecimal characters; mixed case
    is normalized to lowercase before deduplication.
  - `blockhashes` (comma-separated fallback,
    `?blockhashes=<h1>,<h2>,<h3>`). May be combined with repeated
    `blockhash`; the two are deduplicated together while preserving
    request order (first occurrence wins).
  - `start_time` / `end_time` / `time_field` are honoured when
    supplied: any **payable main-chain** block (see `stale_blockhashes`
    below) whose selected time field falls outside the half-open
    `[start_time, end_time)` interval is excluded and its hash is
    reported in `filtered_out_blockhashes`. Stale / non-main-chain
    hashes never appear in `filtered_out_blockhashes`.
  - **Per-request cap:** 500 unique hashes
    (`AZ_REWARD_BLOCKHASH_LOOKUP_TOO_LARGE` returns 422 above the
    cap). Invalid hash format returns 422
    `AZ_REWARD_BLOCKHASH_INVALID`.
  - **Ownership precheck is bypassed in lookup mode** — the caller
    has already named the exact blocks they want, so an
    unconfigured `AZ_REWARD_OWNERSHIP_*` does not produce
    `AZ_REWARD_OWNERSHIP_NOT_CONFIGURED`. Classification fields
    (`is_owned_reward`, `ownership_match`,
    `matched_output_indexes`) are still emitted on every entry when
    config is present.
- **Top-level response metadata (every mode):**
  - **Ledger rule:** treat **`blocks[]` as the only payable, maturity-
    eligible reward truth.** Do not ingest accounting rows from
    `stale_blockhashes`, `unresolved_blockhashes`, or
    `filtered_out_blockhashes` (those are operator/diagnostic signals
    only).
  - `lookup_mode`: `"scan"` | `"blockhashes"`.
  - `requested_blockhash_count`: number of unique hashes the caller
    asked for (always `0` in scan mode).
  - `resolved_blockhash_count`: number of entries returned in
    `blocks` (`== len(blocks)`).
  - `unresolved_blockhashes`: hashes the RPC reported as
    not-found / invalid per-hash application errors (e.g. Bitcoin
    Core `-5 Block not found`). A single bad hash does not crash the
    whole response.
  - `stale_blockhashes`: **blockhash-lookup mode only** (always `[]` in
    scan mode). Hashes for which `getblock` returned a block object
    and strict coinbase validation **passed**, but the block is **not**
    active-chain reward truth: `confirmations <= 0` and/or
    `is_on_main_chain` is false (typical Core pattern: `confirmations:
    -1` for stale/orphan blocks). Such blocks are **not** included in
    `blocks[]`; they are **not payable** and **not maturity-eligible**.
    They must **not** be listed in `unresolved_blockhashes` or
    `filtered_out_blockhashes`.
  - `filtered_out_blockhashes`: hashes for payable main-chain blocks
    that were excluded only because the optional time window did not
    apply (or whose selected time field was missing / non-int).
- **Strict-coinbase invariant:** strict validation runs on every lookup
  payload with a valid height before stale vs. payable classification.
  Malformed coinbases raise 502 `AZ_RPC_INVALID_PAYLOAD` and are
  **never** silently demoted to `stale_blockhashes` or
  `unresolved_blockhashes`. Ledger truth fails loudly.
- **Transport invariant:** `AzcoinRpcTransportError` /
  `AzcoinRpcHttpError` / `AzcoinRpcWrongChainError` propagate to the
  standard `AZ_RPC_UNAVAILABLE` (502) / `AZ_WRONG_CHAIN` (503)
  envelopes; lookup mode never silently swallows transport failures
  into `unresolved_blockhashes`.

### 2.2 `GET /v1/translator/downstreams`

- **Truth role:** translator local-worker truth (section 1.2).
- **Auth:** protected.
- **What it does:** allowlisted passthrough to the translator's
  monitoring server at `GET /api/v1/sv1/clients`, with `offset` /
  `limit` paging. Returns per-downstream-connection rows including
  `client_id`, `authorized_worker_name`, the upstream `channel_id` it
  is currently bound to, and connection-level counters.
- **Source:** translator monitoring HTTP server.

### 2.3 `GET /v1/translator/upstream/channels`

- **Truth role:** translator local-work truth (section 1.2).
- **Auth:** protected.
- **What it does:** allowlisted passthrough to the translator's
  monitoring server at `GET /api/v1/server/channels`. Returns one row
  per upstream pool channel with `channel_id`, `share_work_sum`,
  `shares_submitted` / `shares_acknowledged` / `shares_rejected`,
  `blocks_found`, `best_diff`, `target_hex`.
- **Source:** translator monitoring HTTP server.

These two translator endpoints together carry every field needed for
the new join endpoint defined in section 5.

---

## 3. Endpoint status

The table below classifies every endpoint mentioned in this document.
"Keep" means we don't touch the production code now; "Add" means new
endpoint to be implemented in a follow-up PR; "Deprecate / rename"
means cosmetic / contract-level rename without breaking behavior;
"Later" means out of scope for this MVP.

| Endpoint                                              | Truth role     | Status           |
|-------------------------------------------------------|----------------|------------------|
| `GET /v1/az/blocks/rewards`                           | chain          | Keep             |
| `GET /v1/translator/downstreams`                      | translator     | Keep             |
| `GET /v1/translator/upstream/channels`                | translator     | Keep             |
| `owned_only` query param + `AZ_REWARD_OWNERSHIP_*`    | (chain filter) | Deprecate/rename |
| `GET /v1/translator/miner-work/snapshot`              | translator     | Add              |
| `GET /v1/translator/blocks-found`                     | translator     | Add              |
| `POST /v1/ledger/intervals`                           | ledger         | Add              |
| `POST /v1/ledger/intervals/{id}/snapshots/start`      | ledger         | Add              |
| `POST /v1/ledger/intervals/{id}/snapshots/end`        | ledger         | Add              |
| `POST /v1/ledger/intervals/{id}/close`                | ledger         | Add              |
| `POST /v1/ledger/intervals/{id}/ingest-rewards`       | ledger         | Add              |
| `GET /v1/ledger/intervals/{id}`                       | ledger         | Add              |
| `GET /v1/ledger/intervals/{id}/accounting-preview`    | ledger         | Add              |
| `POST /v1/ledger/rewards/refresh-maturity`            | ledger         | Add              |
| `GET /v1/translator/block-candidates`                 | translator     | Later            |

All "Add" endpoints are protected by the same bearer-auth scheme used
elsewhere in the service. Errors must use the existing structured
envelope (`{"detail": {"code": "...", "message": "..."}}`).

---

## 4. Deprecation note: `owned_only` and `AZ_REWARD_OWNERSHIP_*`

### 4.1 What's wrong with the current naming

Today `/v1/az/blocks/rewards` accepts `owned_only=true` and reads two
env vars:

- `AZ_REWARD_OWNERSHIP_ADDRESSES`
- `AZ_REWARD_OWNERSHIP_SCRIPT_PUBKEYS`

The route classifies a block as "owned" when its coinbase pays an
address (or hits a scriptPubKey hex) listed in those env vars.

In the **current** topology that classification cannot mean
"this SC node produced this block" because every SC node pays into the
*same* shared pool wallet (section 1.4). At best it means
"this block paid into the configured pool wallet(s)" — which is a
useful chain filter but is not ownership.

### 4.2 Resolution: keep behavior, fix the contract

Production code is unchanged by this document. The behavior is fine.
What changes here is the **contract**: how the parameter is named,
documented, and explained.

Recommended path (to be implemented in a follow-up PR, **not now**):

- Rename the env vars (with a temporary alias to avoid breaking
  existing deployments):
  - `AZ_REWARD_OWNERSHIP_ADDRESSES` → `AZ_REWARD_POOL_ADDRESSES`
  - `AZ_REWARD_OWNERSHIP_SCRIPT_PUBKEYS` → `AZ_REWARD_POOL_SCRIPT_PUBKEYS`
- Rename the query param:
  - `owned_only` → `pool_only` (preferred) **or** `reward_wallet_only`.
- Rename the response fields that currently use "owned" wording:
  - `is_owned_reward` → `paid_pool_wallet`
  - `ownership_match` → `pool_wallet_match`
  - `matched_output_indexes` → unchanged.
- Rename the error code:
  - `AZ_REWARD_OWNERSHIP_NOT_CONFIGURED` →
    `AZ_REWARD_POOL_WALLET_NOT_CONFIGURED`.
- Top-level response field `ownership_configured` →
  `pool_wallet_configured`.

### 4.3 If we keep the old names temporarily

Any docs that still describe the existing parameters (including the
README) must include the following caveat verbatim:

> ⚠️ `owned_only` and `AZ_REWARD_OWNERSHIP_*` filter blocks whose
> coinbase paid into a configured **pool wallet**. They do **not**
> identify which SC node produced the block. Per-SC-node attribution
> requires translator-side block-candidate logging (section 7) and is
> not provided by `/v1/az/blocks/rewards`.

### 4.4 What is *not* changing in this PR

- The on-disk env var names.
- The query param name on the route.
- The shape of the response.
- Any tests.

This document is design-only. The rename is a separate, mechanical PR.

---

## 5. Add endpoint spec — `GET /v1/translator/miner-work/snapshot`

### 5.1 Purpose

Provide a single server-side join of the two translator monitoring
sources (`/api/v1/sv1/clients` and `/api/v1/server/channels`) keyed by
`channel_id`. This is the canonical "what each worker is doing right
now" view that the ledger snapshot endpoints (section 6) will read
from.

Doing the join server-side means callers see one stable shape rather
than having to call two endpoints, page through both, and reconcile
`channel_id` themselves on every poll.

### 5.2 Contract

- **Method / path:** `GET /v1/translator/miner-work/snapshot`
- **Auth:** protected (bearer token).
- **Truth role:** translator local-work truth (section 1.2). Pure live
  read — no DB writes, no chain RPC.
- **Query params:**
  - `offset: int = 0` — paging offset into the joined rows
    (default 0, ge 0).
  - `limit: int = 100` — max rows (default 100, ge 1, le 500).
  - `channel_id: str | None` — optional exact filter on a single
    channel.

### 5.3 Behavior

1. Read the translator monitoring `/api/v1/server/channels` payload to
   get the per-upstream-channel counters.
2. Read the translator monitoring `/api/v1/sv1/clients` payload to get
   per-downstream-connection rows including
   `authorized_worker_name` and the upstream `channel_id` each is
   currently bound to.
3. Left-join channels onto downstreams by `channel_id`. A channel with
   no currently-connected downstream still appears with
   `worker_identity = null`.
4. If `channel_id` is supplied, filter to exactly that one channel.
5. If translator monitoring is unconfigured, return the standard
   `{"status": "unconfigured", "configured": false, ...}` envelope —
   same shape as the existing translator passthroughs.

### 5.4 Response shape (success)

```json
{
  "status": "ok",
  "configured": true,
  "captured_at": "2026-04-27T18:05:00Z",
  "rows": [
    {
      "channel_id": "ch-7",
      "worker_identity": "alice.rig01",
      "client_id": "sv1-42",
      "authorized_worker_name": "alice.rig01",
      "share_work_sum": "12345678901234567890",
      "shares_submitted": 8421,
      "shares_acknowledged": 8400,
      "shares_rejected": 21,
      "blocks_found": 0,
      "best_diff": "65536.5",
      "target_hex": "00000000ffff0000000000000000000000000000000000000000000000000000"
    }
  ],
  "total_rows": 1,
  "offset": 0,
  "limit": 100
}
```

### 5.5 Response shape (translator unconfigured)

```json
{
  "status": "unconfigured",
  "configured": false,
  "captured_at": null,
  "rows": [],
  "total_rows": 0,
  "offset": 0,
  "limit": 100
}
```

### 5.6 Field semantics

| Field                     | Source                                           | Notes                                                                                                                          |
|---------------------------|--------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| `channel_id`              | `/api/v1/server/channels`                        | Stable per upstream pool channel.                                                                                              |
| `worker_identity`         | derived from `authorized_worker_name`            | Defined as "the authorized worker name on the most recently authorized downstream bound to this channel," or null if none.    |
| `client_id`               | `/api/v1/sv1/clients`                            | Translator-internal SV1 connection id; null if no downstream bound.                                                            |
| `authorized_worker_name`  | `/api/v1/sv1/clients`                            | Raw value as reported by the translator. Same as `worker_identity` today; kept separately so future identity overrides don't lose the raw signal. |
| `share_work_sum`          | `/api/v1/server/channels`                        | Stringified to preserve full precision; arbitrary-width integer.                                                               |
| `shares_*`                | `/api/v1/server/channels`                        | Submitted / acknowledged / rejected counters.                                                                                  |
| `blocks_found`            | `/api/v1/server/channels`                        | Block-difficulty shares produced; **not** confirmed chain inclusion.                                                           |
| `best_diff`               | `/api/v1/server/channels`                        | String-formatted decimal to avoid float drift.                                                                                 |
| `target_hex`              | `/api/v1/server/channels`                        | Big-endian 64-hex-char target.                                                                                                 |

### 5.7 Errors

The endpoint inherits the translator passthrough error model:

- Translator monitoring unconfigured → `200` with `status: unconfigured`
  envelope (see 5.5). Not an HTTP error.
- Translator HTTP transport failure → `200` with `status: degraded`,
  `rows: []`. Same as existing translator routes.
- Auth failures → `401` from the existing middleware.

---

## 6. Add ledger endpoint specs — `/v1/ledger/*`

### Translator Blocks Found Evidence (`GET /v1/translator/blocks-found`)

- **Truth role:** translator local-work evidence.
- **Auth:** protected.
- **What it does:** exposes durable API-side event evidence written by a
  separate poller process that repeatedly reads
  `/v1/translator/miner-work/snapshot` and persists every positive
  `blocks_found` counter delta for a stable miner identity.
- **Source:** this API's SQLite store at
  `TRANSLATOR_BLOCKS_FOUND_DB_PATH`, populated by
  `python -m node_api.services.translator_blocks_found_poller`.
- **What each row means:** "at observation time `detected_time`, this
  worker identity's translator-side `blocks_found` counter increased
  from `blocks_found_before` to `blocks_found_after`."
- **What it does *not* mean:** by itself it does **not** prove chain
  inclusion, block reward maturity, payout eligibility, wallet
  movement, or an exact blockhash. The ledger must still verify rewards
  through `/v1/az/blocks/rewards`.
- **Identity rule:** `worker_identity` / `authorized_worker_name` is the
  miner identity. `channel_id` is metadata only and must not be used as
  payout identity because reconnects can move the same miner to a new
  channel id.
- **Current correlation status:** the initial implementation is
  `counter_delta_only`; `blockhash` remains null and
  `blockhash_status="unresolved"` unless direct evidence is added in a
  later revision.

---

### 6.1 Data model overview (informative)

The following is the conceptual model the ledger endpoints expose.
Storage details (table layout, ids) are deferred to the implementation
PR.

- **Interval** — a contiguous time window with one of four states:
  `open` → `start_captured` → `end_captured` → `closed`. Has unique
  `id`, `start_time`, optional `end_time`, and `time_field` (matches
  the chain time-window semantics).
- **Snapshot** — a per-channel work-counter capture taken at exactly
  one of two moments: `kind ∈ {"start", "end"}`. Snapshot rows are
  immutable once written.
- **Reward event** — a chain block (height, blockhash, coinbase total
  in satoshis, maturity status) attached to an interval after it falls
  inside `[start_time, end_time)` of that interval.

### 6.2 `POST /v1/ledger/intervals`

Create a new interval and put it in `open` state. No snapshots, no
rewards yet.

**Request:**

```json
{
  "start_time": 1714200000,
  "end_time": 1714200600,
  "time_field": "time",
  "label": "2026-04-27 17:20Z 10-minute window"
}
```

- `start_time` (int, required, ≥0) — Unix seconds.
- `end_time` (int | null, optional) — Unix seconds; if omitted the
  interval is open-ended until the operator calls
  `/snapshots/end`. Must be `> start_time` if provided.
- `time_field` ("time" | "mediantime", default "time") — which block
  time drives reward ingestion (section 6.6). Same semantics as
  `/v1/az/blocks/rewards`.
- `label` (string, optional) — human-readable note.

**Response (201):**

```json
{
  "id": "int_01HZQX5KQK7Q6Y8VN0JT5ZX7AB",
  "state": "open",
  "start_time": 1714200000,
  "end_time": 1714200600,
  "time_field": "time",
  "label": "2026-04-27 17:20Z 10-minute window",
  "created_at": "2026-04-27T17:20:01Z",
  "snapshots": {"start": null, "end": null},
  "ingested_reward_count": 0,
  "closed_at": null
}
```

**Errors:**

- `422 AZ_LEDGER_INTERVAL_INVALID` — bad `start_time`/`end_time`.

### 6.3 `POST /v1/ledger/intervals/{id}/snapshots/start`

Capture per-channel counters at T0 by calling
`/v1/translator/miner-work/snapshot` server-side and persisting the
result against this interval.

**Request body:** none (the route reads live translator state).

**Response (200):**

```json
{
  "interval_id": "int_01HZQX5KQK7Q6Y8VN0JT5ZX7AB",
  "snapshot_id": "snap_01HZQX5KW0ENVT3MM2K0AAQVVD",
  "kind": "start",
  "captured_at": "2026-04-27T17:20:02Z",
  "row_count": 14,
  "interval_state": "start_captured"
}
```

**Errors:**

- `404 AZ_LEDGER_INTERVAL_NOT_FOUND` — unknown id.
- `409 AZ_LEDGER_INTERVAL_BAD_STATE` — interval is not in `open`
  state.
- `503 AZ_TRANSLATOR_UNCONFIGURED` — translator monitoring not
  configured. **No snapshot is written** in this case.

### 6.4 `POST /v1/ledger/intervals/{id}/snapshots/end`

Capture per-channel counters at T1. Same shape as 6.3 with
`kind: "end"`. Requires `interval.state == "start_captured"` and
transitions to `end_captured`.

### 6.5 `POST /v1/ledger/intervals/{id}/close`

Finalize the interval. After close, no new snapshots, no new reward
ingestion. Counter deltas (end − start) are computed at this point.

**Request body:** none.

**Response (200):**

```json
{
  "interval_id": "int_01HZQX5KQK7Q6Y8VN0JT5ZX7AB",
  "interval_state": "closed",
  "closed_at": "2026-04-27T17:30:09Z",
  "deltas_row_count": 14
}
```

**Errors:**

- `409 AZ_LEDGER_INTERVAL_BAD_STATE` — interval not in
  `end_captured`.

### 6.6 `POST /v1/ledger/intervals/{id}/ingest-rewards`

Pull reward events for this interval's `[start_time, end_time)` window
from the chain API and attach them to the interval. Idempotent:
re-running with the same window will not duplicate already-attached
blocks (matched by `(height, blockhash)` pair).

The route internally consumes `/v1/az/blocks/rewards` with the
interval's `start_time`, `end_time`, and `time_field`. It does not
re-implement reward truth.

**Shared-pool deployments (recommended).** When the deployment uses a
shared pool wallet (section 1.4), reward attribution from chain-side
coinbase inspection is structurally impossible. In that case the
ledger should not rely on `owned_only` filtering; instead it should:

1. Collect block hashes from translator block-found evidence
   (today: prefer `/v1/translator/blocks-found` for durable counter-
   delta evidence and correlate it with any direct translator evidence
   available; future: `/v1/translator/block-candidates`, section 7).
2. Call `/v1/az/blocks/rewards` in **blockhash-lookup mode**
   (`?blockhash=<h>` repeated, or `?blockhashes=<h1>,<h2>`) with the
   interval's optional time window applied for sanity. This
   verifies each translator-claimed block exists on the active chain
   and returns its strict coinbase total in satoshis without
   scanning a height range.

Hashes the RPC cannot resolve appear in `unresolved_blockhashes` on
the response and should be surfaced verbatim to the operator (likely
indicates a chain reorg or a translator-side false-positive).
Hashes that resolve but are not on the active main chain (stale /
orphan) appear in `stale_blockhashes` only — **do not** treat them as
payable rewards; the ledger must still use only `blocks[]` for truth.

**Request:**

```json
{
  "include_immature": true
}
```

- `include_immature` (bool, default true) — if false, only blocks with
  `maturity_status == "mature"` are attached.

**Response (200):**

```json
{
  "interval_id": "int_01HZQX5KQK7Q6Y8VN0JT5ZX7AB",
  "attached": 2,
  "skipped_existing": 0,
  "skipped_immature": 0,
  "rewards": [
    {
      "height": 18432,
      "blockhash": "0000...",
      "time": 1714200120,
      "coinbase_total_sats": 5000000000,
      "maturity_status": "immature",
      "blocks_until_mature": 99
    }
  ]
}
```

**Errors:**

- `409 AZ_LEDGER_INTERVAL_BAD_STATE` — interval is `closed` *and*
  `include_immature=false` is not the only safe combination
  (implementation will document precisely which transitions are
  allowed; minimum requirement is "open / start_captured /
  end_captured" all permit ingest, `closed` rejects with this code).
- `502 AZ_RPC_UNAVAILABLE` — chain RPC failure (propagated).
- `422 AZ_REWARD_TIME_RANGE_TOO_LARGE` — chain scan guard tripped.

### 6.7 `GET /v1/ledger/intervals/{id}`

Read everything attached to an interval: state, both snapshots'
metadata (not full rows; those are reachable via the preview endpoint
or a future `/snapshots/{id}` route), and ingested reward events.

**Response (200):**

```json
{
  "id": "int_01HZQX5KQK7Q6Y8VN0JT5ZX7AB",
  "state": "closed",
  "start_time": 1714200000,
  "end_time": 1714200600,
  "time_field": "time",
  "label": "2026-04-27 17:20Z 10-minute window",
  "created_at": "2026-04-27T17:20:01Z",
  "closed_at": "2026-04-27T17:30:09Z",
  "snapshots": {
    "start": {
      "id": "snap_01HZQX5KW0ENVT3MM2K0AAQVVD",
      "captured_at": "2026-04-27T17:20:02Z",
      "row_count": 14
    },
    "end": {
      "id": "snap_01HZQX5T1A3XV5MM2K0AAQYYX",
      "captured_at": "2026-04-27T17:30:00Z",
      "row_count": 14
    }
  },
  "rewards": {
    "count": 2,
    "coinbase_total_sats": 10000000000,
    "mature_count": 0,
    "immature_count": 2
  }
}
```

**Errors:**

- `404 AZ_LEDGER_INTERVAL_NOT_FOUND`.

### 6.8 `GET /v1/ledger/intervals/{id}/accounting-preview`

Compute the proportional split implied by the captured snapshot deltas
and the attached rewards. **Read-only. No money is moved.** This is
the canonical place to ask "what would the books say if we paid out
right now?"

**Query params:**

- `split_rule: Literal["share_work_proportional"] = "share_work_proportional"` —
  for MVP only one rule is defined; documented for forward
  compatibility.
- `include_immature: bool = false` — by default the preview only
  counts mature reward sats so the number is conservative.

**Response (200):**

```json
{
  "interval_id": "int_01HZQX5KQK7Q6Y8VN0JT5ZX7AB",
  "split_rule": "share_work_proportional",
  "include_immature": false,
  "total_share_work_delta": "97300000000",
  "total_reward_sats": 0,
  "total_reward_sats_breakdown": {
    "mature": 0,
    "immature": 10000000000
  },
  "rows": [
    {
      "channel_id": "ch-7",
      "worker_identity": "alice.rig01",
      "share_work_delta": "60000000000",
      "share_pct": "0.61664...",
      "hypothetical_reward_sats": 0
    },
    {
      "channel_id": "ch-9",
      "worker_identity": "bob.rig02",
      "share_work_delta": "37300000000",
      "share_pct": "0.38335...",
      "hypothetical_reward_sats": 0
    }
  ],
  "caveats": [
    "split_rule=share_work_proportional uses snapshot end-minus-start deltas",
    "include_immature=false; immature reward sats are excluded from the total",
    "no payout has been executed; this is a preview only"
  ]
}
```

Notes on the math, to keep implementations honest:

- `share_pct` is `share_work_delta / total_share_work_delta` rendered
  to a fixed precision string (suggest 18 fractional digits).
- `hypothetical_reward_sats` must be computed in integer sats using
  proportional rounding-down per worker, with the dust remainder
  reported on the response (a future revision of this spec will add a
  `dust_sats` field; MVP can omit it as long as
  `sum(hypothetical_reward_sats) <= total_reward_sats`).

**Errors:**

- `404 AZ_LEDGER_INTERVAL_NOT_FOUND`.
- `409 AZ_LEDGER_INTERVAL_BAD_STATE` — interval is not at least
  `end_captured` (you cannot preview a split before both snapshots
  exist).

### 6.9 `POST /v1/ledger/rewards/refresh-maturity`

Rescan currently-immature reward events that have been ingested into
**any** interval and update their `maturity_status` and
`blocks_until_mature` fields by re-querying the chain API. Independent
of any specific interval — this is a periodic maintenance call.

**Request:**

```json
{
  "max_rewards": 500
}
```

- `max_rewards` (int, default 500, ge 1, le 5000) — upper bound on how
  many reward rows the refresh will rescan in one call.

**Response (200):**

```json
{
  "scanned": 42,
  "promoted_to_mature": 7,
  "still_immature": 35,
  "orphaned": 0,
  "errors": 0
}
```

`orphaned` covers blocks whose `confirmations` is now `-1` (no longer
on the active chain). The implementation must mark such rows
explicitly rather than silently leaving them at their last known
state.

**Errors:**

- `502 AZ_RPC_UNAVAILABLE` — chain RPC failure (propagated).

---

## 7. Future endpoint — `GET /v1/translator/block-candidates`

**Status: Later. Do not implement until the dependency below holds.**

### 7.1 Purpose

Per-block attribution: "which downstream worker on this SC node found
candidate block X?" This is the only path to honest per-worker reward
attribution under a shared pool wallet (section 1.4), because chain
RPC alone cannot tell us.

### 7.2 Hard prerequisite

The translator process must persist **block candidate events** —
write-once records emitted whenever a downstream submits a share that
meets network target. Each record must carry at minimum:

- `blockhash` (or full block-header hash candidate)
- `template_id` / job context
- `job_id`
- `channel_id`
- `worker_identity` (authorized worker name at submit time)
- `submitted_at` (timestamp)

If the translator does not log these events to a durable location
(disk, structured log, sidecar DB, or its monitoring server), this
endpoint **must not** be added — there is nothing to read.

### 7.3 Sketch (for forward compatibility only)

```http
GET /v1/translator/block-candidates?since_height=18000&limit=100
```

Response: list of candidate events with the fields above. Exact shape
will be specified when the translator-side persistence lands.

Until then, ledger consumers should treat all attribution as
"share-work-proportional within the SC node," which is what
`accounting-preview` produces in section 6.8.

---

## 8. Non-goals

The following are explicitly **out of scope** for the MVP and must
not be silently introduced through any of the endpoints above:

- **No payout execution.** No endpoint moves coins. Not now, not in
  any "Add"-status route in this document.
- **No wallet movement.** This service does not call wallet RPC for
  send/sign/broadcast flows on the ledger surface. Existing
  `/v1/az/wallet/*` read-only routes are unrelated.
- **No on-chain per-miner balances.** Workers are not addresses on the
  chain. The ledger is an off-chain accounting record only.
- **No exact blockhash-to-worker attribution** unless and until the
  translator persists candidate events (section 7).
- **No hidden accounting or payout state in the translator evidence
  store.** `/v1/translator/blocks-found` persists only translator
  counter-delta evidence; it does not create balances or spend funds.

---

## 9. Acceptance criteria

This document satisfies the brief when **all** of the following are
true:

1. The reader can answer "which endpoint owns reward truth, work
   truth, and accounting truth?" by reading section 1 alone.
2. The reader understands why coinbase address cannot identify the
   producing SC node under the shared pool wallet, and does not leave
   thinking `owned_only=true` means "my SC node mined it" — section
   1.4 + section 4.
3. Every "Add"-status endpoint in section 3 has at least one request
   example (where applicable) and at least one success response
   example, plus its error envelope codes — sections 5 and 6.
4. Existing `owned_only` / `AZ_REWARD_OWNERSHIP_*` semantics are
   marked as misleading and a concrete rename path is given —
   section 4.
5. Non-goals are explicit so reviewers don't have to infer them —
   section 8.

---

## Appendix A — Glossary

- **SC node** — a self-contained "Satoshi Coin" node deployment running
  this API alongside an AZCoin core, an SRI translator, and (today) a
  shared pool wallet. Multiple SC nodes can run in parallel.
- **Pool wallet** — the wallet that receives coinbase payouts. In the
  current topology it is shared across SC nodes.
- **Channel** — an upstream pool channel on the translator
  (`channel_id` in `/api/v1/server/channels`).
- **Downstream** — an SV1 client connected to this translator
  (`client_id` in `/api/v1/sv1/clients`).
- **Worker identity** — the authorized worker name on a downstream,
  e.g. `alice.rig01`. Not a chain identity.
- **Share work** — accumulated weighted share difficulty, the canonical
  proportional unit for off-chain accounting.

## Appendix B — Open questions

The following are intentionally unresolved by this document. Each one
will be answered by the corresponding implementation PR.

- B1. Interval id format: opaque ULID/UUID vs. monotonic integer.
  Either is fine; recommend ULID for log-friendliness.
- B2. Snapshot row durability: do we keep full per-channel rows
  forever, or roll up after the interval closes? Recommend "keep raw
  for at least N intervals" where N is configurable.
- B3. Dust handling in `accounting-preview`: who keeps the satoshi
  remainders after proportional rounding? Recommend "report dust
  explicitly; do not redistribute in MVP."
- B4. Reorg policy on already-ingested rewards: do we re-attach if a
  height changes blockhash? Recommend "yes — attach by `(height,
  blockhash)` and orphan the old row via
  `/rewards/refresh-maturity`."
- B5. Whether `accounting-preview` should be allowed before
  `interval.state == closed` (currently spec says `end_captured`
  suffices). Trade-off: earlier preview vs. risk of consuming
  unfinalized deltas.

---

*End of document.*
