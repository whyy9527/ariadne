"""
Scans backend *Client.java files for outbound HTTP calls via RestClient / RestTemplate.

Detects:
  restClient.get/post/put/delete/patch()
  + pathSegment("seg1", "seg2", ...) or uri("path")

Reconstructs the path from pathSegment chains. The target service is inferred
from the client class name using a caller-supplied substring → service map
(see `client_target_map` kwarg, configured per-repo in ariadne.config.json).
"""
import re
from pathlib import Path
from scanner import BaseScanner


class BackendClientScanner(BaseScanner):
    """Scan backend *Client.java files for outbound HTTP calls."""

    def __init__(self, client_target_map: dict | None = None):
        self.client_target_map = client_target_map

    def scan(self, repo_path: str, service: str) -> list[dict]:
        return scan_backend_clients(repo_path, service, self.client_target_map)


def scan_backend_clients(
    repo_path: str,
    service: str,
    client_target_map: dict | None = None,
) -> list[dict]:
    target_map = client_target_map or {}
    nodes = []
    repo = Path(repo_path)

    client_files = [
        f for f in repo.rglob("*Client.java")
        if "dto" not in str(f) and "Config" not in f.name
        and "exception" not in str(f) and "Impl" not in f.name
        and "src/main" in str(f)
    ]

    for fpath in client_files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        target_svc = _infer_target(fpath.stem, target_map)
        nodes.extend(_parse_client(text, service, target_svc, str(fpath)))

    return nodes


def _infer_target(class_name: str, target_map: dict) -> str:
    lower = class_name.lower()
    for key, svc in target_map.items():
        if key.lower() in lower:
            return svc
    return "external"


def _parse_client(text: str, caller_service: str, target_service: str, source_file: str) -> list[dict]:
    """
    Parse methods that contain a restClient.METHOD() call.
    Each public method = one node.
    """
    nodes = []

    # Find all public method blocks
    method_pattern = re.compile(
        r'public\s+\S+\s+(\w+)\s*\([^)]*\)\s*\{',
    )
    # Find all restClient calls with associated method chain
    rest_call_pattern = re.compile(
        r'restClient\.(get|post|put|delete|patch)\s*\(\)',
        re.IGNORECASE
    )

    for method_match in method_pattern.finditer(text):
        method_name = method_match.group(1)
        if method_name in ("toString", "equals", "hashCode", "getClass"):
            continue

        # Extract the method body (simple: next { ... } block)
        body_start = method_match.end() - 1
        body = _extract_block(text, body_start)
        if not body:
            continue

        # Find restClient call
        rc_match = rest_call_pattern.search(body)
        if not rc_match:
            continue

        http_method = rc_match.group(1).upper()

        # Reconstruct path from pathSegment chain
        path = _extract_path(body)

        node_id = f"{caller_service}::client::{http_method}::{path}::{method_name}"
        nodes.append({
            "id": node_id,
            "type": "backend_client_call",
            "raw_name": method_name,
            "service": caller_service,
            "target_service": target_service,
            "source_file": source_file,
            "fields": _extract_path_vars(path),
            "method": http_method,
            "path": path,
        })

    return nodes


def _extract_block(text: str, start: int) -> str:
    """Extract content of { } block starting at `start`."""
    depth = 0
    end = start
    for i in range(start, min(start + 3000, len(text))):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    return text[start:end]


def _extract_path(body: str) -> str:
    """Reconstruct URL path from pathSegment() calls."""
    segments = re.findall(r'pathSegment\s*\(([^)]+)\)', body)
    if segments:
        parts = []
        for seg_group in segments:
            # Each pathSegment call can have multiple args
            for arg in re.split(r',\s*', seg_group):
                arg = arg.strip()
                # Determine if this is a string literal BEFORE stripping quotes
                is_literal = arg.startswith('"') or arg.startswith("'")
                arg = arg.strip('"\'')
                if not arg:
                    continue
                if is_literal:
                    parts.append(arg)           # literal path segment: "classrooms"
                elif re.match(r'^[a-zA-Z_]\w*$', arg):
                    parts.append(f'{{{arg}}}')  # variable reference: classroomId → {classroomId}
                # else: skip (numbers, etc.)
        return '/' + '/'.join(p for p in parts if p)

    # Fallback: look for uri("path") or plain string path
    uri_match = re.search(r'\.uri\s*\(\s*["\']([^"\']+)["\']', body)
    if uri_match:
        return uri_match.group(1)

    return "/unknown"


def _extract_path_vars(path: str) -> list[str]:
    return re.findall(r'\{(\w+)\}', path)
