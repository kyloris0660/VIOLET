"""Private bounded I/O evidence; public callers retain stable string codes."""

from __future__ import annotations

import time
from typing import Any


class SourceReadReason(str):
    def __new__(cls, code: str, diagnostic: dict[str, Any]):
        value = super().__new__(cls, code)
        value.diagnostic = diagnostic
        return value

    def __reduce__(self):
        return type(self), (str(self), self.diagnostic)


def exception_detail(exc: BaseException, *, stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "exception_type": type(exc).__name__,
        "message": str(exc)[:1024],
        "errno": getattr(exc, "errno", None),
        "winerror": getattr(exc, "winerror", None),
    }


def worker_detail(payload: Any, *, stage: str, status: str, started: float,
                  timeout: float, exitcode: int | None) -> dict[str, Any]:
    detail = dict(payload) if isinstance(payload, dict) else {
        "exception_type": None, "errno": None, "winerror": None,
        "message": str(payload)[:1024] if payload is not None else None,
    }
    detail.update(stage=stage, worker_status=status, exitcode=exitcode,
                  elapsed_seconds=round(time.monotonic() - started, 6), timeout_seconds=timeout)
    return detail
