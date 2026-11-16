"""Exact, shared identifiers for tenant-owned data; never normalize identity."""
import re

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def validate_identifier(value):
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("Invalid identifier")
    return value
