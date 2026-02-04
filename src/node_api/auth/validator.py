from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TokenValidator(Protocol):
    def validate(self, token: str) -> bool: ...


@dataclass(frozen=True)
class StaticTokenValidator:
    """
    Placeholder validator.

    This is intentionally simple and pluggable: swap this implementation for a
    real JWT validator later (signature verification, audience/issuer, etc).
    """

    expected_token: str

    def validate(self, token: str) -> bool:
        return token == self.expected_token


class RejectAllValidator:
    """
    JWT mode placeholder.

    Keeps behavior fail-closed until a real JWT validator is wired in.
    """

    def validate(self, token: str) -> bool:  # noqa: ARG002
        return False
