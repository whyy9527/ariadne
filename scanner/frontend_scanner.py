"""
Scans frontend TS/TSX files for GraphQL operations (gql`` template literals).
Extracts operation name + top-level selection fields.
"""
import re
from pathlib import Path


def scan_frontend(repo_path: str, service: str) -> list[dict]:
    nodes = []
    repo = Path(repo_path)

    # Scan all .ts/.tsx files (not just graphql.ts)
    ts_files = list(repo.rglob("*.ts")) + list(repo.rglob("*.tsx"))
    ts_files = [f for f in ts_files if "node_modules" not in str(f)]
    for fpath in ts_files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        nodes.extend(_parse_frontend_gql(text, service, str(fpath)))

    return nodes


def _parse_frontend_gql(text: str, service: str, source_file: str) -> list[dict]:
    nodes = []

    # Match gql` ... ` blocks (possibly multiline)
    gql_blocks = re.findall(r'gql`(.*?)`', text, re.DOTALL)

    for block in gql_blocks:
        # Find operation: query/mutation/subscription Name (
        op_match = re.search(
            r'(query|mutation|subscription)\s+(\w+)',
            block, re.IGNORECASE
        )
        # Anonymous operations: query { fieldName(... }
        anon_match = re.search(
            r'(query|mutation|subscription)\s*\(',
            block, re.IGNORECASE
        ) if not op_match else None

        if op_match:
            op_kind = op_match.group(1).lower()
            op_name = op_match.group(2)
        elif anon_match:
            op_kind = anon_match.group(1).lower()
            # Try to infer name from first selection field
            field_m = re.search(r'\{[^{]*?(\w+)\s*[({]', block)
            op_name = field_m.group(1) if field_m else "anonymous"
        else:
            # Bare query body without operation keyword
            field_m = re.search(r'\{\s*(\w+)', block)
            op_name = field_m.group(1) if field_m else "unknown"
            op_kind = "query"

        # Top-level fields selected
        top_fields = re.findall(r'^\s{4,8}(\w+)\s*[{(\n]', block, re.MULTILINE)
        top_fields = [f for f in top_fields if f not in ("query", "mutation", "subscription")]

        node_type_map = {
            "query": "frontend_query",
            "mutation": "frontend_mutation",
            "subscription": "frontend_subscription",
        }

        nodes.append({
            "id": f"{service}::frontend::{op_kind}::{op_name}",
            "type": node_type_map.get(op_kind, "frontend_query"),
            "raw_name": op_name,
            "service": service,
            "source_file": source_file,
            "fields": top_fields,
            "method": None,
            "path": None,
        })

    return nodes
