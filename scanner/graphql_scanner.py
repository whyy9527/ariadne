"""
Scans .gql / .graphql files.
Extracts:
  - type definitions + their fields
  - Query / Mutation / Subscription operations
"""
import re
from pathlib import Path
from scanner import BaseScanner


class GraphQLScanner(BaseScanner):
    """Scan .gql / .graphql schema files."""

    def scan(self, repo_path: str, service: str) -> list[dict]:
        return scan_graphql_files(repo_path, service)


def scan_graphql_files(repo_path: str, service: str) -> list[dict]:
    nodes = []
    repo = Path(repo_path)
    gql_files = list(repo.rglob("*.gql")) + list(repo.rglob("*.graphql"))

    for fpath in gql_files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        nodes.extend(_parse_gql(text, service, str(fpath)))

    return nodes


def _parse_gql(text: str, service: str, source_file: str) -> list[dict]:
    nodes = []

    # Strip comments
    text_no_comments = re.sub(r'#.*', '', text)

    # Match type blocks: type Foo { ... } or extend type Foo { ... }
    # Also handles Query/Mutation/Subscription
    type_pattern = re.compile(
        r'(?:extend\s+)?type\s+(\w+)(?:\s+implements\s+\w+)?\s*\{([^}]*)\}',
        re.DOTALL
    )

    for m in type_pattern.finditer(text_no_comments):
        type_name = m.group(1)
        body = m.group(2)
        fields = _extract_fields(body)

        if type_name in ("Query", "Mutation", "Subscription"):
            # Each field is an operation
            op_type_map = {
                "Query": "graphql_query",
                "Mutation": "graphql_mutation",
                "Subscription": "graphql_subscription",
            }
            node_type = op_type_map[type_name]
            for op_name, op_args in fields:
                nodes.append({
                    "id": f"{service}::gql::{type_name}::{op_name}",
                    "type": node_type,
                    "raw_name": op_name,
                    "service": service,
                    "source_file": source_file,
                    "fields": op_args,
                    "method": None,
                    "path": None,
                })
        else:
            # Regular type definition
            field_names = [f[0] for f in fields]
            nodes.append({
                "id": f"{service}::gql::type::{type_name}",
                "type": "graphql_type",
                "raw_name": type_name,
                "service": service,
                "source_file": source_file,
                "fields": field_names,
                "method": None,
                "path": None,
            })

    return nodes


def _extract_fields(body: str) -> list[tuple[str, list[str]]]:
    """Return list of (field_name, [arg_names]) from a type body."""
    fields = []
    # Match field lines: fieldName(args): ReturnType or fieldName: ReturnType
    field_pattern = re.compile(r'(\w+)\s*(?:\([^)]*\))?\s*:', re.MULTILINE)
    for m in field_pattern.finditer(body):
        name = m.group(1)
        # Extract arg names from parens if present
        args = []
        paren_match = re.search(r'\(([^)]*)\)', body[m.start():m.end()+50])
        if paren_match:
            for arg in re.finditer(r'(\w+)\s*:', paren_match.group(1)):
                args.append(arg.group(1))
        fields.append((name, args))
    return fields
