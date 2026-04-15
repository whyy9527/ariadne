"""
Scans Java/Kotlin *Controller files for REST endpoints.
Detects @RestController + @{Get,Post,Put,Delete,Patch}Mapping annotations.
"""
import re
from pathlib import Path
from scanner import BaseScanner


class HTTPScanner(BaseScanner):
    """Scan Java/Kotlin Controller files for REST endpoints."""

    def scan(self, repo_path: str, service: str) -> list[dict]:
        return scan_http_controllers(repo_path, service)


METHOD_ANNOTATIONS = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
    "RequestMapping": "ANY",
}


def scan_http_controllers(repo_path: str, service: str) -> list[dict]:
    nodes = []
    repo = Path(repo_path)
    java_files = (
        list(repo.rglob("*Controller.java")) +
        list(repo.rglob("*Controller.kt")) +
        list(repo.rglob("*Router.kt"))   # Kotlin functional routing
    )

    for fpath in java_files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        nodes.extend(_parse_controller(text, service, str(fpath)))

    return nodes


def _parse_controller(text: str, service: str, source_file: str) -> list[dict]:
    nodes = []

    # Class-level base path
    class_mapping = re.search(
        r'@RequestMapping\s*\(\s*["\']?([^"\')\s]+)["\']?\s*\)',
        text
    )
    base_path = ""
    if class_mapping:
        base_path = class_mapping.group(1).strip('"\'/').strip()

    # Find all method-level mappings
    # Supports Java: public ReturnType methodName(
    # Supports Kotlin: fun methodName(
    pattern = re.compile(
        r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)'
        r'\s*\(?\s*(?:value\s*=\s*)?["\']?([^"\')\n]*)["\']?\s*\)?'
        r'.*?'
        r'(?:'
            r'(?:public|private|protected|override)\s+[\w<>\[\],\s]+\s+(\w+)\s*\('  # Java
            r'|'
            r'fun\s+(\w+)\s*\('                                                        # Kotlin
        r')',
        re.DOTALL
    )

    for m in pattern.finditer(text):
        annotation = m.group(1)
        path_part = m.group(2).strip().strip('"\'').strip().lstrip('/')
        method_name = m.group(3) or m.group(4)  # group 3=Java, group 4=Kotlin

        http_method = METHOD_ANNOTATIONS.get(annotation, "ANY")

        # Build full path
        full_path = "/" + "/".join(
            p for p in [base_path, path_part] if p
        ).replace("//", "/")

        # Extract path variables as "fields"
        path_vars = re.findall(r'\{(\w+)\}', full_path)

        node_id = f"{service}::http::{http_method}::{full_path}::{method_name}"
        nodes.append({
            "id": node_id,
            "type": "http_endpoint",
            "raw_name": method_name,
            "service": service,
            "source_file": source_file,
            "fields": path_vars,
            "method": http_method,
            "path": full_path,
        })

    return nodes
