"""
Miner-work snapshot service for the SC-node ledger.

This module joins the two raw translator monitoring sources -- upstream
SV2 channel counters (``/api/v1/server/channels``) and downstream SV1
client identities (``/api/v1/sv1/clients``) -- into one stable, normalized
shape keyed by ``channel_id``. It is intended as the canonical
"what each worker is doing right now" view that the future ledger
interval-snapshot endpoints (``/v1/ledger/intervals/.../snapshots/...``)
will read from. See ``docs/api/ledger-mvp-endpoints.md`` for the contract.

Truth role (per the architecture rule):

* This file produces TRANSLATOR LOCAL-WORK TRUTH.
* It does NOT consume the chain RPC, the wallet RPC, or any DB.
* It MUST NOT be used to identify which SC node mined a block: under the
  shared pool wallet topology, coinbase-side data cannot answer that.

Numeric policy (intentional, ledger-driven):

* Money / accounting-sensitive counters that future ledger arithmetic will
  consume -- ``share_work_sum``, ``best_diff``, ``hashrate``,
  ``nominal_hashrate`` -- are stringified so we never lose precision to
  IEEE-754 drift downstream.
* Plain integer counters (shares_*, blocks_found, channel_id, client_id)
  stay integers.
* All other values are passed through as strings or normalized booleans.

Fail-closed policy:

* If translator monitoring is not configured, return an
  ``unconfigured`` envelope with an empty ``items`` list.
* If exactly one of the two raw fetches fails, the join would produce
  half-truth ledger data. We refuse: return a ``degraded`` envelope with
  an empty ``items`` list and a ``detail`` string identifying which side
  failed. (See task brief: "prefer fail-closed ... rather than returning
  partial ledger data".)
"""

from __future__ import annotations

import time
from typing import Any

from node_api.services import translator_monitoring as tm
from node_api.settings import Settings

# When the SC node has more downstream SV1 clients than this, the snapshot
# will silently truncate. 500 matches the upper bound already enforced on
# the existing ``GET /v1/translator/downstreams`` route; if a deployment
# routinely exceeds it, this endpoint will need a paged variant.
_DOWNSTREAMS_PAGE_LIMIT = 500

# The translator's monitoring server has historically wrapped lists under
# any of these keys depending on endpoint and version. Be tolerant.
_LIST_KEY_FALLBACKS: tuple[str, ...] = (
    "extended_channels",
    "channels",
    "clients",
    "items",
    "data",
)

# Field-name fallbacks. The ``_first_present`` helper walks each tuple in
# order and returns the first non-None hit. Keeping these as constants at
# module scope (rather than literals at call sites) makes it cheap to
# review the full set of accepted shapes in one place.
_UPSTREAM_CHANNEL_ID_KEYS = ("channel_id", "id", "channel")
_DOWNSTREAM_CHANNEL_ID_KEYS = ("channel_id", "channel", "upstream_channel_id")
_DOWNSTREAM_CLIENT_ID_KEYS = ("client_id", "id")
_DOWNSTREAM_AUTHORIZED_NAME_KEYS = ("authorized_worker_name", "authorized_name")
_DOWNSTREAM_USER_IDENTITY_KEYS = ("user_identity", "user_id")
_UPSTREAM_USER_IDENTITY_KEYS = ("user_identity", "user_id")
_TARGET_HEX_KEYS = ("target_hex", "target", "current_target_hex")
_SHARES_ACK_KEYS = ("shares_acknowledged", "shares_acked")
_SHARES_SUB_KEYS = ("shares_submitted",)
_SHARES_REJ_KEYS = ("shares_rejected",)
_SHARE_WORK_SUM_KEYS = ("share_work_sum",)
_BEST_DIFF_KEYS = ("best_diff", "best_difficulty")
_BLOCKS_FOUND_KEYS = ("blocks_found",)
_HASHRATE_KEYS = ("hashrate",)
_NOMINAL_HASHRATE_KEYS = ("nominal_hashrate",)
_EXTRANONCE1_HEX_KEYS = ("extranonce1_hex", "extranonce1")
_EXTRANONCE_PREFIX_HEX_KEYS = ("extranonce_prefix_hex", "extranonce_prefix")
_EXTRANONCE2_LEN_KEYS = ("extranonce2_len", "extranonce2_size")
_FULL_EXTRANONCE_SIZE_KEYS = ("full_extranonce_size", "extranonce_total_size")
_ROLLABLE_EXTRANONCE_SIZE_KEYS = ("rollable_extranonce_size",)
_VERSION_ROLLING_KEYS = ("version_rolling", "version_rolling_supported")
_VERSION_ROLLING_MASK_KEYS = ("version_rolling_mask", "version_mask")
_VERSION_ROLLING_MIN_BIT_KEYS = (
    "version_rolling_min_bit",
    "version_min_bit_count",
)


def _extract_list(payload: Any) -> list[dict[str, Any]]:
    """Return the items list from a translator monitoring payload.

    Accepts a bare list (``[{...}, {...}]``) or a dict that wraps the list
    under one of ``_LIST_KEY_FALLBACKS``. Non-dict items are silently
    dropped; bad payloads collapse to an empty list rather than raising.
    """
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in _LIST_KEY_FALLBACKS:
            v = payload.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def _first_present(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first non-None value among ``d[keys]``, else ``None``."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _to_int(v: Any) -> int | None:
    """Coerce to int. Bool and unparseable strings become ``None``."""
    if isinstance(v, bool):
        # bool is a subclass of int; reject so True/False don't leak in as 1/0.
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None
    return None


def _to_str_numeric(v: Any) -> str | None:
    """Stringify a ledger-sensitive numeric value.

    The point is to preserve full precision across the API boundary so
    downstream ledger code can use ``Decimal`` / arbitrary-width int math
    without ever round-tripping through float. Bools are rejected (they
    would render as ``"True"`` / ``"False"``, which is not numeric).
    Floats render via ``repr`` to preserve as many digits as Python emits
    -- still imprecise, but at least stable and not hidden.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        s = v.strip()
        return s if s else None
    if isinstance(v, float):
        return repr(v)
    return None


def _to_str_passthrough(v: Any) -> str | None:
    """Pass strings through unchanged; coerce other plain scalars."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return str(v)
    return None


def _to_bool(v: Any) -> bool | None:
    """Accept only true booleans; everything else is ``None``."""
    if isinstance(v, bool):
        return v
    return None


def _index_by_channel_id(
    items: list[dict[str, Any]], channel_id_keys: tuple[str, ...]
) -> dict[int, dict[str, Any]]:
    """Index items by their (int-coerced) channel_id.

    First-wins on duplicates within a single side -- the join is 1:1 by
    contract, so ties are silently resolved to the first-seen entry. Items
    whose channel_id is missing or non-numeric are dropped (they cannot
    participate in the join).
    """
    indexed: dict[int, dict[str, Any]] = {}
    for it in items:
        ch = _to_int(_first_present(it, channel_id_keys))
        if ch is None:
            continue
        if ch not in indexed:
            indexed[ch] = it
    return indexed


def _resolve_worker_identity(
    authorized: str | None, downstream_user: str | None
) -> str | None:
    """Apply the ``worker_identity`` resolution rule.

    Per the API contract:
      1. Prefer downstream ``authorized_worker_name``.
      2. Fall back to downstream ``user_identity``.
      3. Otherwise ``None``.

    A whitespace-only string at either step is treated as missing.
    """
    if authorized is not None and authorized.strip():
        return authorized
    if downstream_user is not None and downstream_user.strip():
        return downstream_user
    return None


def _make_item(
    channel_id: int,
    downstream: dict[str, Any] | None,
    upstream: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one normalized snapshot row for a single ``channel_id``.

    Either side may be ``None`` -- ``join_status`` records which case we
    landed in. The output dict matches the ``MinerWorkSnapshotItem``
    pydantic model in the route module.
    """
    if downstream is not None and upstream is not None:
        join_status = "joined"
    elif downstream is not None:
        join_status = "downstream_only"
    else:
        join_status = "upstream_only"

    d = downstream or {}
    u = upstream or {}

    authorized = _to_str_passthrough(
        _first_present(d, _DOWNSTREAM_AUTHORIZED_NAME_KEYS)
    )
    downstream_user = _to_str_passthrough(
        _first_present(d, _DOWNSTREAM_USER_IDENTITY_KEYS)
    )
    upstream_user = _to_str_passthrough(
        _first_present(u, _UPSTREAM_USER_IDENTITY_KEYS)
    )

    return {
        "channel_id": channel_id,
        "client_id": _to_int(_first_present(d, _DOWNSTREAM_CLIENT_ID_KEYS)),
        "worker_identity": _resolve_worker_identity(authorized, downstream_user),
        "authorized_worker_name": authorized,
        "downstream_user_identity": downstream_user,
        "upstream_user_identity": upstream_user,
        # Integer counters (kept as ints for cheap delta math in the ledger).
        "shares_acknowledged": _to_int(_first_present(u, _SHARES_ACK_KEYS)),
        "shares_submitted": _to_int(_first_present(u, _SHARES_SUB_KEYS)),
        "shares_rejected": _to_int(_first_present(u, _SHARES_REJ_KEYS)),
        # String-stringified numerics (precision-sensitive for the ledger).
        "share_work_sum": _to_str_numeric(_first_present(u, _SHARE_WORK_SUM_KEYS)),
        "best_diff": _to_str_numeric(_first_present(u, _BEST_DIFF_KEYS)),
        "blocks_found": _to_int(_first_present(u, _BLOCKS_FOUND_KEYS)),
        "hashrate": _to_str_numeric(_first_present(u, _HASHRATE_KEYS)),
        "nominal_hashrate": _to_str_numeric(
            _first_present(u, _NOMINAL_HASHRATE_KEYS)
        ),
        # Targets are kept distinct on purpose: the downstream sees a
        # pdiff-derived target, the upstream tracks the pool's target.
        "downstream_target_hex": _to_str_passthrough(
            _first_present(d, _TARGET_HEX_KEYS)
        ),
        "upstream_target_hex": _to_str_passthrough(
            _first_present(u, _TARGET_HEX_KEYS)
        ),
        "extranonce1_hex": _to_str_passthrough(
            _first_present(d, _EXTRANONCE1_HEX_KEYS)
        ),
        "extranonce_prefix_hex": _to_str_passthrough(
            _first_present(u, _EXTRANONCE_PREFIX_HEX_KEYS)
        ),
        "extranonce2_len": _to_int(_first_present(d, _EXTRANONCE2_LEN_KEYS)),
        "full_extranonce_size": _to_int(
            _first_present(u, _FULL_EXTRANONCE_SIZE_KEYS)
        ),
        "rollable_extranonce_size": _to_int(
            _first_present(u, _ROLLABLE_EXTRANONCE_SIZE_KEYS)
        ),
        "version_rolling": _to_bool(_first_present(d, _VERSION_ROLLING_KEYS)),
        "version_rolling_mask": _to_str_passthrough(
            _first_present(d, _VERSION_ROLLING_MASK_KEYS)
        ),
        "version_rolling_min_bit": _to_str_passthrough(
            _first_present(d, _VERSION_ROLLING_MIN_BIT_KEYS)
        ),
        "join_status": join_status,
    }


def _empty_envelope(
    status: str, *, configured: bool, detail: str | None
) -> dict[str, Any]:
    """Build an envelope with an empty items list (used on unconfigured / degraded)."""
    return {
        "status": status,
        "configured": configured,
        "snapshot_time": None,
        "source": "translator",
        "data": {"total": 0, "items": []},
        "detail": detail,
    }


def _format_side_detail(side: str, resp: dict[str, Any]) -> str:
    """Render a compact ``side:reason`` token for the degraded ``detail`` string."""
    reason = resp.get("detail") or resp.get("status") or "unknown"
    return f"{side}:{reason}"


def build_miner_work_snapshot(settings: Settings) -> dict[str, Any]:
    """Produce a normalized miner-work snapshot envelope.

    See module docstring for the truth-role and fail-closed policy. The
    return value is a plain dict so the route layer can run it through the
    ``MinerWorkSnapshotResponse`` pydantic model for validation.
    """
    if not tm.is_monitoring_configured(settings):
        return _empty_envelope("unconfigured", configured=False, detail=None)

    # Two raw fetches reusing the existing allowlisted-passthrough helper.
    # No new HTTP code paths and no new allowlist entries: both paths are
    # already in ``translator_monitoring._EXACT_PATHS``.
    channels_resp = tm.fetch_allowlisted(settings, "/api/v1/server/channels", None)
    clients_resp = tm.fetch_allowlisted(
        settings,
        "/api/v1/sv1/clients",
        {"offset": 0, "limit": _DOWNSTREAMS_PAGE_LIMIT},
    )

    # Fail-closed: do not return half-joined data. If either side is
    # unavailable, the ledger should retry rather than read truncated work.
    if channels_resp["status"] != "ok" or clients_resp["status"] != "ok":
        details: list[str] = []
        if channels_resp["status"] != "ok":
            details.append(_format_side_detail("upstream_channels", channels_resp))
        if clients_resp["status"] != "ok":
            details.append(_format_side_detail("downstreams", clients_resp))
        return _empty_envelope(
            "degraded", configured=True, detail=";".join(details) or None
        )

    upstream_items = _extract_list(channels_resp.get("data"))
    downstream_items = _extract_list(clients_resp.get("data"))

    upstream_by_ch = _index_by_channel_id(upstream_items, _UPSTREAM_CHANNEL_ID_KEYS)
    downstream_by_ch = _index_by_channel_id(
        downstream_items, _DOWNSTREAM_CHANNEL_ID_KEYS
    )

    # Sort by channel_id ascending so callers polling this endpoint get a
    # stable row order independent of dict iteration order.
    all_channel_ids = sorted(set(upstream_by_ch) | set(downstream_by_ch))
    items = [
        _make_item(ch, downstream_by_ch.get(ch), upstream_by_ch.get(ch))
        for ch in all_channel_ids
    ]

    return {
        "status": "ok",
        "configured": True,
        "snapshot_time": int(time.time()),
        "source": "translator",
        "data": {"total": len(items), "items": items},
        "detail": None,
    }
