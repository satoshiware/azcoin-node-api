# Changelog

## [0.2.0] - 2026-04-27

- Added `GET /v1/az/blocks/rewards` with strict integer-satoshi coinbase totals (`Decimal(str(value)) * 100_000_000`, no rounding) and per-output details (`index`, `value_sats`, `address`, `script_type`, `script_pub_key_hex`).
- Added per-block maturity fields: `is_mature`, `blocks_until_mature`, `maturity_status`, `maturity_height`.
- Added safe `is_on_main_chain` derivation from RPC `confirmations` (true only when the value is a non-negative integer).
- Added time-window filtering on `/v1/az/blocks/rewards` (`start_time`, `end_time`, `time_field=time|mediantime`) with half-open interval semantics, a 5000-block scan cap (`AZ_REWARD_TIME_RANGE_TOO_LARGE`), and `mediantime`-driven early termination.
- Added reward ownership classification driven by `AZ_REWARD_OWNERSHIP_ADDRESSES` and `AZ_REWARD_OWNERSHIP_SCRIPT_PUBKEYS`, including `is_owned_reward`, `matched_output_indexes`, `ownership_match`, `ownership_configured`, and the `owned_only` query param. Documented as configured pool/reward-wallet filtering only — does not identify SC-node ownership under a shared pool wallet.
- Added blockhash-lookup mode on `/v1/az/blocks/rewards`: repeatable `?blockhash=<64hex>` and CSV `?blockhashes=<h1>,<h2>` (max 500 unique hashes per request, 422 `AZ_REWARD_BLOCKHASH_LOOKUP_TOO_LARGE`; invalid format returns 422 `AZ_REWARD_BLOCKHASH_INVALID`). Skips height scan, ignores `limit`, bypasses the ownership precheck, applies the optional time window, and surfaces partial failures via `unresolved_blockhashes` / `filtered_out_blockhashes` without crashing the response. Strict coinbase validation and transport error envelopes (`AZ_RPC_UNAVAILABLE` / `AZ_WRONG_CHAIN`) preserved.
- Added top-level response metadata on `/v1/az/blocks/rewards` for both modes: `lookup_mode`, `requested_blockhash_count`, `resolved_blockhash_count`, `unresolved_blockhashes`, `filtered_out_blockhashes`, `time_filter`.
- Added `GET /v1/translator/miner-work/snapshot` — ledger-ready normalized join of upstream channel counters and downstream miner identity, keyed by `channel_id`. Ledger-sensitive numerics (`share_work_sum`, `best_diff`, `hashrate`, `nominal_hashrate`) are returned as strings; counters as integers; `worker_identity` resolved with `authorized_worker_name → user_identity → null`. Fail-closed `degraded` shape when either side is unreachable.
- Deprecated `GET /v1/translator/runtime`, `/global`, `/upstream`, `/upstream/channels`, `/downstreams`, `/downstreams/{client_id}` (raw monitoring passthroughs; OpenAPI `deprecated: true`, behavior unchanged). Prefer `/v1/translator/status` and `/v1/translator/miner-work/snapshot`.
- Deprecated `GET /v1/events/recent-legacy` (pre-EventStore in-memory ZMQ buffer; OpenAPI `deprecated: true`). Canonical: EventStore-backed `GET /v1/events/recent`.
- Added `docs/api/ledger-mvp-endpoints.md` — Chain / Translator / Ledger truth-role split, shared-pool-wallet caveat, current and proposed endpoint contracts, deprecation note proposing future `AZ_REWARD_POOL_*` / `pool_only` renames, and the recommended shared-pool flow (translator block-found events → blockhash-lookup mode).

## [0.1.7] - 2026-04-13

- Added AZ read-only endpoints: peers, mempool info, wallet summary, and wallet transactions.
- Added `since` validation with stable errors: `AZ_INVALID_SINCE` (`422`) and `AZ_SINCE_NOT_FOUND` (`404`).
- Added deterministic wallet transaction ordering (newest-first by `time`) with post-sort `limit`.
- Added chain guardrail enforcement with `AZ_EXPECTED_CHAIN` and `AZ_WRONG_CHAIN` (`503`).
- Changed AZ RPC client behavior to support generic result types and centralized wrong-chain detection.
- Expanded mocked RPC tests for endpoint contracts and schema drift defense.
