"""
Normalizer: lowercase + split camelCase/snake_case/kebab-case into tokens.
Keeps raw_name. No aggressive synonym merging.
"""
import re


def split_tokens(name: str) -> list[str]:
    """Split camelCase, PascalCase, snake_case, kebab-case, dot.case into tokens."""
    # Insert space before uppercase sequences (camel/Pascal)
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', s)
    # Replace separators
    s = re.sub(r'[-_./]', ' ', s)
    tokens = [t.lower() for t in s.split() if len(t) > 1]
    return tokens


def normalize(raw_name: str, fields: list[str] = None) -> dict:
    tokens = split_tokens(raw_name)
    field_tokens = []
    if fields:
        for f in fields:
            field_tokens.extend(split_tokens(f))
    return {
        "raw_name": raw_name,
        "tokens": list(dict.fromkeys(tokens)),          # deduplicated, ordered
        "field_tokens": list(dict.fromkeys(field_tokens)),
    }
