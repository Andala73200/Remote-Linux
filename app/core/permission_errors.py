from __future__ import annotations


_PERMISSION_MARKERS = (
    "permission denied",
    "permission non accordée",
    "permission non accordee",
    "operation not permitted",
    "opération non permise",
    "operation non permise",
    "errno 13",
)


def is_permission_denied(value: object) -> bool:
    text = str(value or "").casefold()
    return any(marker in text for marker in _PERMISSION_MARKERS)
