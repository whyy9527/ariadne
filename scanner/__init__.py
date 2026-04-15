"""
scanner package — built-in scanner modules + BaseScanner interface.

Custom scanners
---------------
Implement ``BaseScanner`` and reference the class by dotted path in
``ariadne.config.json``:

    {"type": "my_pkg.my_scanner:MyScanner", "some_option": "value"}

The remaining keys (everything except ``"type"``) are passed as keyword
arguments to ``__init__``.  The class must implement ``scan(repo_path, service)``
returning a ``list[dict]`` in the same node format as the built-in scanners.
"""
from abc import ABC, abstractmethod


class BaseScanner(ABC):
    """Minimal interface every scanner must satisfy.

    Built-in scanners are plain functions and are **not** required to subclass
    ``BaseScanner`` (they pre-date the ABC).  Third-party / custom scanners
    declared via dotted path in config **must** subclass ``BaseScanner``.

    Node dict format (required keys)
    ---------------------------------
    id          : str   – unique "<service>::<type>::<name>" key
    type        : str   – one of the known node type strings
    raw_name    : str   – human-readable name (used for tokenisation)
    service     : str   – repo name from config
    source_file : str | None
    method      : str | None   – HTTP verb, or None
    path        : str | None   – URL path, or None
    fields      : list[str]    – optional field names (default [])
    """

    @abstractmethod
    def scan(self, repo_path: str, service: str) -> list[dict]:
        """Scan *repo_path* and return a list of node dicts."""
