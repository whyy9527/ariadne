"""
Single source of truth for Ariadne's shared documentation fragments.

Two consumers read from this module:
  1. mcp_server._build_help_text()  — runtime, always fresh
  2. README.md                       — hand-pasted; test_semantic_hint.py
     verifies README contains every fragment verbatim

Workflow when editing a fragment:
  1. Edit the constant (or change argparse for install_usage).
  2. Run `python3 test_semantic_hint.py`.
  3. On drift, the test prints which fragments are missing and the command
     to dump them. Copy the new text into README.md by hand.
  4. Re-run tests. Commit both files.

No marker blocks, no sync script, no CI — the test is the only guard.
"""
from __future__ import annotations


def install_usage() -> str:
    """
    Install subcommand usage line, derived from main.build_parser().

    `prog` is forced to a stable string so the output doesn't depend on
    sys.argv[0] (which is "-c" under `python -c`, "mcp_server.py" under
    stdio MCP, etc.).
    """
    try:
        from main import build_parser
        parser = build_parser()
        install = parser._ariadne_subparsers["install"]
        install.prog = "python3 main.py install"
        usage = install.format_usage().strip()
        if usage.lower().startswith("usage:"):
            usage = usage[6:].strip()
        return " ".join(usage.split())
    except Exception:
        return "python3 main.py install <config> <workspace-dir> [flags]"


GOLDEN_PATH = """\
Golden path — driving Ariadne from an AI conversation:

  1. query_chains(hint="createOrder")
       → ranked clusters of GraphQL / REST / Kafka / frontend nodes across
         services. Use this first to build cross-service context.

  2. expand_node(name="order-created")
       → one-hop neighbours of a specific node you want to trace.
         If called within 10 minutes of a matching query_chains, Ariadne
         automatically writes a positive feedback row — no extra call
         needed. The follow-up expand IS the signal.

  3. Read the files the returned clusters / neighbours point at.

  4. log_feedback(hint, accepted=False, ...) ONLY when a result was
     misleading. Most feedback is captured implicitly in step 2;
     log_feedback is the manual escape hatch for thumbs-down.

  Staleness: if query_chains or expand_node return a non-null
  `stale_warning` field, call rescan() once — it re-scans the repos
  listed in the install-time config, rebuilds embeddings if needed,
  and clears the warning. Then retry your original query."""


SCANNERS = """\
| Scanner            | Looks for                                                          |
|--------------------|--------------------------------------------------------------------|
| `graphql`          | `.graphql` / `.gql` SDL → Query / Mutation / Subscription / Type   |
| `http`             | Spring `@RestController` (Java/Kotlin) → HTTP endpoints            |
| `kafka`            | Spring `application.yaml` topics + `@KafkaListener` + producers    |
| `backend_clients`  | Spring `RestClient` / `RestTemplate` outbound calls in `*Client.*` |
| `frontend_graphql` | TypeScript `gql\\`\\`` literals → frontend Query/Mutation            |
| `frontend_rest`    | `axios`/`fetch` calls in TS/TSX files, excluding tests/mocks/types |
| `cube`             | cube.js `cube(...)` definitions                                    |"""


def fragments() -> dict[str, str]:
    """Every fragment that must appear verbatim in README.md."""
    return {
        "install_usage": install_usage(),
        "golden_path": GOLDEN_PATH,
        "scanners": SCANNERS,
    }


if __name__ == "__main__":
    # Dump all fragments so you can copy them into README after edits.
    for name, body in fragments().items():
        print(f"=== {name} ===")
        print(body)
        print()
