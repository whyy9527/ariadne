"""
Scans TypeScript files for outbound HTTP calls.

Detects:
  1. Apollo RESTDataSource / MarsLadderDataSource subclasses:
       this.baseURL = settings.<key>.host
       this.setBaseURL(settings.<key>.host)
       baseURL = settings.<key>.host  (property initializer)
     → resolves settings key to a service via `settings_key_map` config option.
       Zero-config fallback: key itself == service name (e.g. "nezha" → "nezha").
       Override only when the key doesn't match the service name
       (e.g. {"userService": "user-service", "intellectualTutoringService": "its"}).

  2. Direct fetch / axios calls:
       axios.get('/path' | url_var)   — method-form
       axios(config)                  — config-object form (method=None)
       fetch('url' | url_var)         — string literal or variable URL
     → target resolved via `url_prefix_map` config option
       (e.g. {"/api/falcon": "falcon"})
     → variable URLs (non-literal) emit node with path=None, target_service=None

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
    "_comment": "settings_key_map: empty = use key as service name; add overrides only when key != service (e.g. userService→user-service)",
    "settings_key_map": {},
    "url_prefix_map": {},
    "client_name_map": {}
  }
"""
import logging
import re
from pathlib import Path
from scanner import BaseScanner

# Module-level set so each fallback settings key logs only once per process run
_logged_fallback_keys: set[str] = set()


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

# axios.get/post/put/delete/patch/head/request('/path' | url_var)
_AXIOS_METHOD_CALL = re.compile(
    r'axios\.(get|post|put|delete|patch|head|request)\s*\(\s*([\'"`]([^\'"`\n]+)[\'"`]|\w+)',
    re.IGNORECASE,
)

# axios(config) — config-object form, e.g. axios({ method: 'post', url: ... })
# Does NOT match axios.method( forms (negative lookbehind for dot).
_AXIOS_CONFIG_CALL = re.compile(
    r'(?<![.\w])axios\s*\(\s*(\w+|\{)',
)

# fetch('url' | url_var) — string literal or identifier; node-fetch or built-in
# Lookbehind prevents matching prefetch, obj.fetch, etc.
_FETCH_CALL = re.compile(
    r'(?<![\w.])fetch\s*\(\s*([\'"`]([^\'"`\n]+)[\'"`]|\w+)',
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
                "/__generated__/", "/generated/", ".generated.ts", "/mock"):
        if seg in path:
            return True
    return False


def _nearest_class_name(text: str, match_pos: int) -> str | None:
    """Return the name of the class declaration nearest before *match_pos*.

    Precomputes nothing — called per match, but files are small so a linear
    backward scan is fine.  Returns None if no class declaration precedes the
    match position.
    """
    best_name: str | None = None
    for m in _CLASS_DECL.finditer(text):
        if m.start() < match_pos:
            best_name = m.group(1)
        else:
            break  # finditer yields in order; no earlier match possible after this
    return best_name


def _scan_file(
    text: str,
    source_file: str,
    service: str,
    settings_key_map: dict,
    url_prefix_map: dict,
    client_name_map: dict,
) -> list[dict]:
    nodes: list[dict] = []

    # Fallback class name used when no class declaration precedes a match.
    # Also used for axios/fetch/client passes (unchanged behaviour there).
    class_names = [m.group(1) for m in _CLASS_DECL.finditer(text)]
    fallback_class_name = class_names[0] if class_names else Path(source_file).stem

    # --- 1. settings.X.host pattern (Apollo DS subclass) ---
    # Use the class declaration nearest *before* each settings match so that
    # files with multiple DS classes attribute each baseURL to the right class.
    for m in _SETTINGS_BASE_URL.finditer(text):
        settings_key = m.group(1)
        target_service = _resolve_settings_key(settings_key, settings_key_map, service)
        cls = _nearest_class_name(text, m.start()) or fallback_class_name
        nodes.append(_make_node(
            service=service,
            name=cls,
            target_service=target_service,
            source_file=source_file,
            method=None,
            path=None,
        ))

    # --- 2a. axios method-form calls (axios.get/post/etc.) ---
    for m in _AXIOS_METHOD_CALL.finditer(text):
        http_method = m.group(1).upper()
        url_or_path = m.group(3) or ""   # group(3) = string literal content; empty if identifier
        target_service = _resolve_url(url_or_path, url_prefix_map)
        cls = _nearest_class_name(text, m.start()) or fallback_class_name
        nodes.append(_make_node(
            service=service,
            name=f"{cls}.{m.group(1).lower()}",
            target_service=target_service,
            source_file=source_file,
            method=http_method,
            path=url_or_path or None,
        ))

    # --- 2b. axios config-object form calls (axios(config)) ---
    for m in _AXIOS_CONFIG_CALL.finditer(text):
        cls = _nearest_class_name(text, m.start()) or fallback_class_name
        nodes.append(_make_node(
            service=service,
            name=f"{cls}.axios",
            target_service=None,
            source_file=source_file,
            method=None,
            path=None,
        ))

    # --- 3. fetch calls (string literal or variable URL) ---
    for m in _FETCH_CALL.finditer(text):
        url = m.group(2) or ""           # group(2) = string literal content; empty if identifier
        target_service = _resolve_url(url, url_prefix_map)
        cls = _nearest_class_name(text, m.start()) or fallback_class_name
        nodes.append(_make_node(
            service=service,
            name=f"{cls}.fetch",
            target_service=target_service,
            source_file=source_file,
            method="GET",
            path=url or None,
        ))

    # --- 4. typed client instantiation ---
    for m in _CLIENT_INSTANTIATION.finditer(text):
        client_cls = m.group(1)
        target_service = _resolve_client(client_cls, client_name_map)
        cls = _nearest_class_name(text, m.start()) or fallback_class_name
        nodes.append(_make_node(
            service=service,
            name=f"{cls}.{client_cls}",
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


def _resolve_settings_key(settings_key: str, settings_key_map: dict, service: str) -> str:
    """Resolve a settings key to a target service.

    Lookup order:
      1. Explicit entry in settings_key_map (override/correction layer).
      2. Zero-config fallback: key itself == service name (logs once per key).
    """
    if settings_key in settings_key_map:
        return settings_key_map[settings_key]
    global _logged_fallback_keys
    if settings_key not in _logged_fallback_keys:
        _logged_fallback_keys.add(settings_key)
        logging.info(
            "ts_http_outbound[%s]: no settings_key_map entry for '%s' — "
            "defaulting target_service='%s'. Add to settings_key_map to override.",
            service, settings_key, settings_key,
        )
    return settings_key


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
