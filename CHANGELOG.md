# Changelog

## [0.1.7] - 2026-04-13

- Added AZ read-only endpoints: peers, mempool info, wallet summary, and wallet transactions.
- Added `since` validation with stable errors: `AZ_INVALID_SINCE` (`422`) and `AZ_SINCE_NOT_FOUND` (`404`).
- Added deterministic wallet transaction ordering (newest-first by `time`) with post-sort `limit`.
- Added chain guardrail enforcement with `AZ_EXPECTED_CHAIN` and `AZ_WRONG_CHAIN` (`503`).
- Changed AZ RPC client behavior to support generic result types and centralized wrong-chain detection.
- Expanded mocked RPC tests for endpoint contracts and schema drift defense.
