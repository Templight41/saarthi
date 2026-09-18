"""Errors raised by the simulated enterprise systems.

`retryable` is what the recovery engine classifies on, so it is part of the
contract rather than an afterthought.
"""

from __future__ import annotations


class EnterpriseAPIError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


def not_found(what: str, ident: str) -> EnterpriseAPIError:
    return EnterpriseAPIError(404, "NOT_FOUND", f"{what} {ident} not found", retryable=False)


def invalid_state(message: str) -> EnterpriseAPIError:
    return EnterpriseAPIError(409, "INVALID_STATE", message, retryable=False)


def upstream_timeout(message: str = "Refund gateway timed out") -> EnterpriseAPIError:
    return EnterpriseAPIError(500, "UPSTREAM_TIMEOUT", message, retryable=True)
