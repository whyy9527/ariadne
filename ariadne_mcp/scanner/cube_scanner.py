"""
Scans cube.js model files for cube() definitions.
Each cube is an analytics query surface: clients POST {dimensions, measures,
filters} against cube-name.field references via the cube.js REST endpoint.

Produces one node per cube, with measures + dimensions as fields.
"""
import re
from pathlib import Path
from ariadne_mcp.scanner import BaseScanner


class CubeScanner(BaseScanner):
    """Scan cube.js model files for cube() definitions."""

    def scan(self, repo_path: str, service: str) -> list[dict]:
        return scan_cubes(repo_path, service)


CUBE_RE = re.compile(
    r'cube\s*\(\s*[`\'"](\w+)[`\'"]\s*,\s*\{(.*?)\n\}\s*\)\s*;?',
    re.DOTALL,
)

SECTION_RE = re.compile(
    r'(measures|dimensions)\s*:\s*\{(.*?)\n\s{2}\}',
    re.DOTALL,
)

FIELD_NAME_RE = re.compile(r'^\s{4}(\w+)\s*:\s*\{', re.MULTILINE)


def scan_cubes(repo_path: str, service: str) -> list[dict]:
    nodes = []
    repo = Path(repo_path)
    model_dir = repo / "model"
    if not model_dir.is_dir():
        return nodes

    for fpath in model_dir.rglob("*.js"):
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        nodes.extend(_parse_cube_file(text, service, str(fpath)))

    return nodes


def _parse_cube_file(text: str, service: str, source_file: str) -> list[dict]:
    nodes = []
    for m in CUBE_RE.finditer(text):
        cube_name = m.group(1)
        body = m.group(2)

        fields: list[str] = []
        for sec in SECTION_RE.finditer(body):
            section_body = sec.group(2)
            fields.extend(FIELD_NAME_RE.findall(section_body))

        node_id = f"{service}::cube::{cube_name}"
        nodes.append({
            "id": node_id,
            "type": "cube_query",
            "raw_name": cube_name,
            "service": service,
            "source_file": source_file,
            "fields": fields,
        })

    return nodes
