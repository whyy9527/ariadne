"""Deterministic FastAPI route scanner using only the Python AST."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

from ariadne_mcp.scanner import BaseScanner


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
EXCLUDED_PARTS = {".git", ".venv", "venv", "build", "dist", "node_modules", "__pycache__"}


class FastAPIScanner(BaseScanner):
    """Extract literal FastAPI and APIRouter decorator routes."""

    def scan(self, repo_path: str, service: str) -> list[dict]:
        return scan_fastapi_routes(repo_path, service)


def scan_fastapi_routes(repo_path: str, service: str) -> list[dict]:
    repo = Path(repo_path)
    nodes: list[dict] = []
    for source_file in sorted(repo.rglob("*.py")):
        if any(part in EXCLUDED_PARTS for part in source_file.parts):
            continue
        try:
            source = source_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=str(source_file))
        except (OSError, SyntaxError) as exc:
            print(f"[ariadne] fastapi: skipped {source_file}: {exc}", file=sys.stderr)
            continue
        nodes.extend(_scan_tree(tree, service, str(source_file)))
    return nodes


def _scan_tree(tree: ast.AST, service: str, source_file: str) -> list[dict]:
    constructor_names: set[str] = set()
    module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "fastapi":
            for alias in node.names:
                if alias.name in {"FastAPI", "APIRouter"}:
                    constructor_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "fastapi":
                    module_names.add(alias.asname or alias.name)

    route_owners: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call) or not _is_constructor(value.func, constructor_names, module_names):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                route_owners.add(target.id)

    nodes = []
    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    functions.sort(key=lambda node: (node.lineno, node.name))
    for function in functions:
        for decorator in function.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            owner = decorator.func.value
            method = decorator.func.attr.lower()
            if not isinstance(owner, ast.Name) or owner.id not in route_owners or method not in HTTP_METHODS:
                continue
            path_node = decorator.args[0] if decorator.args else next(
                (keyword.value for keyword in decorator.keywords if keyword.arg == "path"),
                None,
            )
            if not isinstance(path_node, ast.Constant) or not isinstance(path_node.value, str):
                print(
                    f"[ariadne] fastapi: skipped dynamic route {source_file}:{decorator.lineno}",
                    file=sys.stderr,
                )
                continue
            path = _normalize_path(path_node.value)
            http_method = method.upper()
            nodes.append({
                "id": f"{service}::http::{http_method}::{path}::{function.name}",
                "type": "http_endpoint",
                "raw_name": function.name,
                "service": service,
                "source_file": source_file,
                "fields": re.findall(r"\{([^}:]+)(?::[^}]+)?\}", path),
                "method": http_method,
                "path": path,
            })
    return nodes


def _is_constructor(function: ast.expr, constructor_names: set[str], module_names: set[str]) -> bool:
    if isinstance(function, ast.Name):
        return function.id in constructor_names
    return (
        isinstance(function, ast.Attribute)
        and function.attr in {"FastAPI", "APIRouter"}
        and isinstance(function.value, ast.Name)
        and function.value.id in module_names
    )


def _normalize_path(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return "/"
    return "/" + stripped.lstrip("/")
