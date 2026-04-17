"""
Scans TypeScript files for outbound HTTP calls.

Detects:
  1. Apollo RESTDataSource / MarsLadderDataSource subclasses:
       this.baseURL = settings.<key>.host
       this.setBaseURL(settings.<key>.host)
       baseURL = settings.<key>.host  (property initializer)
     → resolves settings key to a service via `settings_key_map` config option
       (e.g. {"api": "falcon", "nezha": "nezha", "userService": "user-service"})

  2. Direct fetch / axios calls:
       axios.get('/path' | url)
       fetch('url')
     → target resolved via `url_prefix_map` config option
       (e.g. {"/api/falcon": "falcon"})

  3. Typed client instantiation (best-effort):
       new XClient().method()
     → class name substring matched via `client_name_map`
       (e.g. {"FalconClient": "falcon"})

Emits `backend_client_call` nodes (same type as BackendClientScanner) so
downstream consumers work without changes. Nodes that cannot be resolved to a
target get to_service=null — still discoverable by name.

Config example (in ariadne.config.json):
  {
    "type": "ts_http_outbound",
    "settings_key_map": {
      "api": "falcon",
      "nezha": "nezha",
      "fuxi": "fuxi",
      "userService": "user-service",
      "intellectualTutoringService": "its",
      "queryService": "query-service"
    },
    "url_prefix_map": {
      "http://falcon": "falcon",
      "http://nezha": "nezha"
    },
    "client_name_map": {}
  }
"""
import re
from pathlib import Path
from scanner import BaseScanner


class TsHttpOutboundScanner(BaseScanner):
    """Scan TypeScript files for outbound HTTP calls (Apollo DS, fetch, axios)."""

    def __init__(
        self,
        settings_key_map: dict | None = None,
        url_prefix_map: dict | None = None,
        client_name_map: dict | None = None,
    ):
        self.settings_key_map = settings_key_map or {}
        self.url_prefix_map = url_prefix_map or {}
        self.client_name_map = client_name_map or {}

    def scan(self, repo_path: str, service: str) -> list[dict]:
        return scan_ts_http_outbound(
            repo_path,
            service,
            settings_key_map=self.settings_key_map,
            url_prefix_map=self.url_prefix_map,
            client_name_map=self.client_name_map,
        )


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# this.baseURL = settings.api.host
# this.setBaseURL(settings.api.host)
# baseURL = settings.api.host  (class field initializer)
_SETTINGS_BASE_URL = re.compile(
    r'(?:this\.baseURL\s*=\s*|this\.setBaseURL\s*\(\s*|baseURL\s*=\s*)'
    r'settings\.(\w+)\.host'
)

# Class declaration: export default class FooDsImpl extends RestDs
_CLASS_DECL = re.compile(
    r'(?:export\s+(?:default\s+)?)?class\s+(\w+)\s+extends\s+\w+'
)

# axios.get/post/put/delete/patch('/path' | url_var)
_AXIOS_CALL = re.compile(
    r'axios\.(get|post|put|delete|patch)\s*\(\s*([\'"`]([^\'"`\n]+)[\'"`]|\w+)',
    re.IGNORECASE,
)

# fetch('url') — node-fetch or built-in
_FETCH_CALL = re.compile(
    r'(?<!\w)fetch\s*\(\s*[\'"`]([^\'"`\n]+)[\'"`]',
)

# new XClient().method() — typed client instantiation
_CLIENT_INSTANTIATION = re.compile(
    r'new\s+(\w*[Cc]lient\w*)\s*\('
)

# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan_ts_http_outbound(
    repo_path: str,
    service: str,
    settings_key_map: dict,
    url_prefix_map: dict,
    client_name_map: dict,
) -> list[dict]:
    nodes: list[dict] = []
    repo = Path(repo_path)

    ts_files = [
        f for f in repo.rglob("*.ts")
        if not _is_excluded(str(f))
    ]

    for fpath in ts_files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        nodes.extend(
            _scan_file(text, str(fpath), service, settings_key_map, url_prefix_map, client_name_map)
        )

    return _dedup(nodes)


def _is_excluded(path: str) -> bool:
    for seg in ("/node_modules/", "/dist/", "/__tests__/", ".test.ts", ".spec.ts",
                "/types/", "/mock", "/generated"):
        if seg in path:
            return True
    return False


def _scan_file(
    text: str,
    source_file: str,
    service: str,
    settings_key_map: dict,
    url_prefix_map: dict,
    client_name_map: dict,
) -> list[dict]:
    nodes: list[dict] = []

    # --- 1. settings.X.host pattern (Apollo DS subclass) ---
    # Find the class name this appears in
    class_names = [m.group(1) for m in _CLASS_DECL.finditer(text)]
    class_name = class_names[0] if class_names else Path(source_file).stem

    for m in _SETTINGS_BASE_URL.finditer(text):
        settings_key = m.group(1)
        target_service = settings_key_map.get(settings_key) or None
        nodes.append(_make_node(
            service=service,
            name=class_name,
            target_service=target_service,
            source_file=source_file,
            method=None,
            path=None,
        ))

    # --- 2. axios calls ---
    for m in _AXIOS_CALL.finditer(text):
        http_method = m.group(1).upper()
        url_or_path = m.group(3) or ""
        target_service = _resolve_url(url_or_path, url_prefix_map)
        nodes.append(_make_node(
            service=service,
            name=f"{class_name}.{m.group(1).lower()}",
            target_service=target_service,
            source_file=source_file,
            method=http_method,
            path=url_or_path or None,
        ))

    # --- 3. fetch calls ---
    for m in _FETCH_CALL.finditer(text):
        url = m.group(1)
        target_service = _resolve_url(url, url_prefix_map)
        nodes.append(_make_node(
            service=service,
            name=f"{class_name}.fetch",
            target_service=target_service,
            source_file=source_file,
            method="GET",
            path=url,
        ))

    # --- 4. typed client instantiation ---
    for m in _CLIENT_INSTANTIATION.finditer(text):
        cls = m.group(1)
        target_service = _resolve_client(cls, client_name_map)
        nodes.append(_make_node(
            service=service,
            name=cls,
            target_service=target_service,
            source_file=source_file,
            method=None,
            path=None,
        ))

    return nodes


def _make_node(
    service: str,
    name: str,
    target_service: str | None,
    source_file: str,
    method: str | None,
    path: str | None,
) -> dict:
    tgt_tag = target_service or "unknown"
    node_id = f"{service}::ts_outbound::{name}::{tgt_tag}"
    return {
        "id": node_id,
        "type": "backend_client_call",
        "raw_name": name,
        "service": service,
        "target_service": target_service,  # may be None
        "source_file": source_file,
        "fields": [tgt_tag],
        "method": method,
        "path": path,
    }


def _resolve_url(url: str, url_prefix_map: dict) -> str | None:
    if not url:
        return None
    for prefix, svc in url_prefix_map.items():
        if url.startswith(prefix):
            return svc
    return None


def _resolve_client(class_name: str, client_name_map: dict) -> str | None:
    lower = class_name.lower()
    for key, svc in client_name_map.items():
        if key.lower() in lower:
            return svc
    return None


def _dedup(nodes: list[dict]) -> list[dict]:
    """Deduplicate by id, keeping first occurrence."""
    seen: set[str] = set()
    result: list[dict] = []
    for n in nodes:
        if n["id"] not in seen:
            seen.add(n["id"])
            result.append(n)
    return result
