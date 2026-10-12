"""
Scans Java/Kotlin *Controller files for REST endpoints.
Detects @RestController + @{Get,Post,Put,Delete,Patch}Mapping annotations.
"""
import re
from pathlib import Path
from ariadne_mcp.scanner import BaseScanner, source_files


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
    # Spring / Kotlin naming conventions observed in the wild:
    #   *Controller.{java,kt}   — canonical Spring MVC
    #   *Resource.{java,kt}     — JAX-RS-style, also used by Spring samples
    #                              (e.g. spring-petclinic-microservices)
    #   *Endpoint.{java,kt}     — Spring WebFlux + Actuator convention
    #   *Router.kt              — Kotlin functional routing DSL
    java_files = (
        list(source_files(repo, "*Controller.java")) +
        list(source_files(repo, "*Controller.kt")) +
        list(source_files(repo, "*Resource.java")) +
        list(source_files(repo, "*Resource.kt")) +
        list(source_files(repo, "*Endpoint.java")) +
        list(source_files(repo, "*Endpoint.kt")) +
        list(source_files(repo, "*Router.kt"))
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

    # Restrict the base mapping to the declaration prefix; a method-level
    # RequestMapping must never become the base for every endpoint.
    declaration = re.search(r"\b(?:class|interface)\s+\w+", text)
    prefix = text[:declaration.start()] if declaration else ""
    class_mapping = re.search(r"@RequestMapping\s*\(([^)]*)\)", prefix)
    base_path = _mapping_path(class_mapping.group(1)) if class_mapping else ""
    if base_path is None:
        return []

    # Quoted parentheses (e.g. PreAuthorize("isAuthenticated()")) and
    # multiline value/consumes annotations are common production contracts.
    quoted = r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'"
    args = r"(?:" + quoted + r"|[^()\"']|\([^()]*\))*"
    pattern = re.compile(
        r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)'
        r'(?:\s*\((' + args + r')\))?'
        r'\s*(?:@\w+(?:\(' + args + r'\))?\s*)*'
        r'(?:'
            r'(?:public|private|protected|override)\s+[\w<>\[\],\s]+\s+(\w+)\s*\('
            r'|(?:suspend\s+)?fun\s+(\w+)\s*\('
        r')',
    )

    for m in pattern.finditer(text):
        annotation = m.group(1)
        path_part = _mapping_path(m.group(2) or "")
        if path_part is None:
            continue
        path_part = path_part.lstrip("/")
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


def _mapping_path(args: str) -> str | None:
    """Read the first literal route; do not invent paths from dynamic constants."""
    named = re.search(r"\b(?:value|path)\s*=\s*(.*)", args, re.DOTALL)
    route = named.group(1).lstrip() if named else args.strip()
    literal = re.match(r"[\"']([^\"']*)[\"']", route)
    if literal:
        return literal.group(1).strip("/")
    if not route or (not named and re.match(r"(?:produces|consumes|headers|params)\s*=", route)):
        return ""
    return None
