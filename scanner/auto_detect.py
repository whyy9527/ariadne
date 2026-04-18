"""Auto-detect which scanners apply to a repo from filesystem signals.

Goal: let users write `{"path": "../foo"}` and have Ariadne pick the right
scanner set. Explicit `scanners` in config always wins.

Heuristics (cheap — top-level probes only):

  * cube.js dep in package.json                → ["cube"]
  * package.json + apollo-server / GraphQL SDL → ["graphql", "ts_http_outbound"]  (TS BFF)
  * package.json (no SDL / apollo)             → ["frontend_graphql", "frontend_rest"]
  * pom.xml / build.gradle(.kts)               → ["http", "kafka", "backend_clients"]
    plus "graphql" prepended if a GraphQL SDL file is found (JVM BFF)
  * otherwise                                  → []

Zero config implies zero overrides: every scanner's optional maps default to
empty; see each scanner's __init__. Override by writing the scanner object
explicitly in config.
"""
from __future__ import annotations

import json
import os


_GRAPHQL_SDL_DIRS = ("", "src", "src/schema", "schema", "graphql", "src/graphql")
_GRAPHQL_SDL_EXTS = (".graphql", ".gql")


def _has_graphql_sdl(repo_path: str) -> bool:
    for sub in _GRAPHQL_SDL_DIRS:
        d = os.path.join(repo_path, sub) if sub else repo_path
        if not os.path.isdir(d):
            continue
        try:
            for entry in os.listdir(d):
                if entry.endswith(_GRAPHQL_SDL_EXTS):
                    return True
        except OSError:
            continue
    return False


def _read_package_json(repo_path: str) -> dict | None:
    path = os.path.join(repo_path, "package.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _all_deps(pkg: dict) -> set[str]:
    out: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        out.update((pkg.get(key) or {}).keys())
    return out


def _is_jvm(repo_path: str) -> bool:
    for name in ("pom.xml", "build.gradle", "build.gradle.kts"):
        if os.path.isfile(os.path.join(repo_path, name)):
            return True
    return False


def detect_scanners(repo_path: str) -> list[str]:
    """Return the default scanner name list for *repo_path*.

    Empty list means "unknown repo type — user must specify scanners".
    """
    if not os.path.isdir(repo_path):
        return []

    pkg = _read_package_json(repo_path)
    if pkg is not None:
        deps = _all_deps(pkg)
        if any(d.startswith("@cubejs-backend") for d in deps):
            return ["cube"]
        apollo_server = any(
            d == "apollo-server" or d.startswith("apollo-server-") or d == "@apollo/server"
            for d in deps
        )
        if apollo_server or _has_graphql_sdl(repo_path):
            return ["graphql", "ts_http_outbound"]
        return ["frontend_graphql", "frontend_rest"]

    if _is_jvm(repo_path):
        scanners = ["http", "kafka", "backend_clients"]
        if _has_graphql_sdl(repo_path):
            return ["graphql", *scanners]
        return scanners

    return []
