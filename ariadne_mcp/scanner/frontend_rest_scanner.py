"""
Scans frontend TS/TSX files for REST calls via axios or fetch.
Detects: this.axiosRequest.get/post/put/delete/patch('path', ...)
Also detects direct fetch() calls.

Each call becomes a node of type "frontend_rest". Target service is inferred
from the TypeScript base class via a caller-supplied `base_class_service` map
(set per-repo in ariadne.config.json). Unmatched files default to "unknown".

File filter: all .ts/.tsx files are scanned except noise directories/patterns:
  - node_modules, __mocks__, __tests__, .next, dist, build, coverage (path segments)
  - *.test.ts, *.test.tsx, *.spec.ts, *.spec.tsx
  - *.d.ts (type declarations)
  - *.stories.ts, *.stories.tsx (Storybook)
"""
import re
from pathlib import Path
from ariadne_mcp.scanner import BaseScanner

# Path segments (anywhere in the path) to exclude entirely
_NOISE_PATH_SEGMENTS = frozenset({
    "node_modules", "__mocks__", "__tests__",
    ".next", "dist", "build", "coverage",
})

# Stem suffixes that indicate noise files (.test.ts, .d.ts, etc.)
_NOISE_STEM_SUFFIXES = (
    ".test", ".spec", ".d", ".stories",
)


def _is_noise(f: Path) -> bool:
    parts = set(f.parts)
    if parts & _NOISE_PATH_SEGMENTS:
        return True
    # e.g. "Button.stories" or "api.d" or "foo.test"
    stem = f.stem
    for suffix in _NOISE_STEM_SUFFIXES:
        if stem.endswith(suffix):
            return True
    return False


class FrontendRESTScanner(BaseScanner):
    """Scan frontend TS/TSX files for REST calls via axios/fetch."""

    def __init__(self, base_class_service: dict | None = None):
        self.base_class_service = base_class_service

    def scan(self, repo_path: str, service: str) -> list[dict]:
        return scan_frontend_rest(repo_path, service, self.base_class_service)


def scan_frontend_rest(
    repo_path: str,
    service: str,
    base_class_service: dict | None = None,
) -> list[dict]:
    base_map = base_class_service or {}
    nodes = []
    repo = Path(repo_path)

    ts_files = [
        f
        for ext in ("*.ts", "*.tsx")
        for f in repo.rglob(ext)
        if not _is_noise(f)
    ]

    for fpath in ts_files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        target_svc = _infer_target_service(text, base_map)
        nodes.extend(_parse_rest_calls(text, service, target_svc, str(fpath)))

    return _dedup(nodes)


def _infer_target_service(text: str, base_map: dict) -> str:
    for base_cls, svc in base_map.items():
        if f"extends {base_cls}" in text:
            return svc
    return "unknown"


def _parse_rest_calls(text: str, caller_service: str, target_service: str, source_file: str) -> list[dict]:
    nodes = []

    # 1. this.axiosRequest.METHOD<Type>('path') or this.axiosRequest.METHOD('path')
    axios_pattern = re.compile(
        r'this\.axiosRequest\.(get|post|put|delete|patch)\s*(?:<[^>]*>)?\s*\('
        r'\s*[`\'"](\/[^`\'"]+)[`\'"]',
        re.IGNORECASE
    )
    for m in axios_pattern.finditer(text):
        method = m.group(1).upper()
        path = m.group(2)
        # Find enclosing method name
        method_name = _find_enclosing_method(text, m.start())
        nodes.append(_make_node(method, path, method_name, caller_service, target_service, source_file))

    # 2. fetch('url' or `url`)
    fetch_pattern = re.compile(
        r'await\s+fetch\s*\(\s*[`\'"]([^`\'"]+)[`\'"]',
    )
    for m in fetch_pattern.finditer(text):
        url = m.group(1)
        # Extract path from URL (strip domain if present)
        path = re.sub(r'^https?://[^/]+', '', url)
        if not path.startswith('/'):
            continue
        method_name = _find_enclosing_method(text, m.start())
        nodes.append(_make_node("GET", path, method_name, caller_service, target_service, source_file))

    return nodes


def _find_enclosing_method(text: str, pos: int) -> str:
    """Search backwards from pos to find the enclosing method/function name."""
    snippet = text[:pos]
    # async methodName( or methodName(
    matches = list(re.finditer(
        r'(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*\S+\s*)?\{',
        snippet
    ))
    if matches:
        return matches[-1].group(1)
    return "unknown"


def _make_node(method: str, path: str, method_name: str, caller: str, target: str, source_file: str) -> dict:
    # Clean path: strip template vars for display but keep for fields
    path_vars = re.findall(r'\$\{([^}]+)\}|\{([^}]+)\}|:(\w+)', path)
    clean_path = re.sub(r'\$\{[^}]+\}', '{param}', path)
    clean_path = re.sub(r':\w+', '{param}', clean_path)
    clean_path = clean_path.split('?')[0]  # strip query string

    node_id = f"{caller}::rest::{method}::{clean_path}::{method_name}"
    return {
        "id": node_id,
        "type": "frontend_rest",
        "raw_name": method_name,
        "service": caller,
        "target_service": target,
        "source_file": source_file,
        "fields": [v for group in path_vars for v in group if v],
        "method": method,
        "path": clean_path,
        "meta": {"target_service": target},
    }


def _dedup(nodes: list[dict]) -> list[dict]:
    seen = {}
    for n in nodes:
        if n["id"] not in seen:
            seen[n["id"]] = n
    return list(seen.values())
