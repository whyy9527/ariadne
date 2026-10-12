"""
scanner package — built-in scanner modules + BaseScanner interface.

All built-in scanners (graphql, http, kafka, frontend_graphql, frontend_rest,
backend_clients, cube) subclass ``BaseScanner``.  ``SCANNER_REGISTRY`` in
``main.py`` maps short names to those classes.

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

    All built-in scanners and third-party / custom scanners declared via
    dotted path in config **must** subclass ``BaseScanner``.

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


# One policy for source discovery, including auto-detection. Prune before reading
# so generated copies cannot overwrite authoritative contracts with the same ID.
EXCLUDED_DIRS = frozenset({
    ".git", "node_modules", "dist", "build", "target", "coverage", ".next",
    "__pycache__", ".venv", "venv", "vendor", "__tests__",
})


def source_files(root, pattern="*"):
    """Yield deterministic source paths without traversing generated/vendor trees."""
    import fnmatch
    import os
    from pathlib import Path
    root = Path(root)
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for name in sorted(files):
            path = Path(directory) / name
            if fnmatch.fnmatchcase(name, pattern) and not path.is_symlink():
                yield path
