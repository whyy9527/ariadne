# Configuration

Paths are resolved relative to the config file.

## Minimal form

```json
{ "repos": [
    { "path": "../gateway" },
    { "path": "../orders-svc" },
    { "path": "../web" }
]}
```

Scanners are inferred from each repo's top-level files at scan time.
`name` defaults to the path basename. `bff_services` is derived from
repos whose scanner list contains `graphql`.

Inference is logged at scan start (`[auto-detect] <repo>: scanners = [...]`)
so you see exactly what got filled in. Run
`ariadne-mcp config validate --config <path>` for a dry-run that
prints inferred defaults and flags errors (missing paths, duplicate
names, unknown scanner types, etc.).

## Detection rules

Top-level files in each repo:

| Signal                                          | Inferred scanners                       |
|-------------------------------------------------|-----------------------------------------|
| `package.json` with `@cubejs-backend/*` dep     | `["cube"]`                              |
| `package.json` + `@apollo/server` / SDL file    | `["graphql", "ts_http_outbound"]`       |
| `package.json` (anything else)                  | `["frontend_graphql", "frontend_rest"]` |
| `pom.xml` / `build.gradle(.kts)` + SDL          | `["graphql", "http", "kafka", "backend_clients"]` |
| `pom.xml` / `build.gradle(.kts)`                | `["http", "kafka", "backend_clients"]`  |
| none of the above                               | *warning printed, repo skipped*         |

## Available scanners

| Scanner            | Looks for                                                          |
|--------------------|--------------------------------------------------------------------|
| `graphql`          | `.graphql` / `.gql` SDL → Query / Mutation / Subscription / Type   |
| `http`             | Spring `@RestController` (Java/Kotlin) → HTTP endpoints            |
| `kafka`            | Spring `application.yaml` topics + `@KafkaListener` + producers    |
| `backend_clients`  | Spring `RestClient` / `RestTemplate` outbound calls in `*Client.*` |
| `frontend_graphql` | TypeScript `gql\`\`` literals → frontend Query/Mutation            |
| `frontend_rest`    | `axios`/`fetch` calls in TS/TSX files, excluding tests/mocks/types |
| `cube`             | cube.js `cube(...)` definitions                                    |
| `ts_http_outbound` | Apollo RESTDataSource subclasses + raw fetch/axios in TS BFFs      |

## When defaults aren't enough

Override a single repo by writing an explicit `scanners` list. Use the
object form when a scanner needs mappings that can't be inferred:

```json
{
  "path": "../bff",
  "scanners": [
    "graphql",
    {
      "type": "ts_http_outbound",
      "settings_key_map": { "userService": "user-service" },
      "url_prefix_map":   { "http://orders": "orders-svc" }
    }
  ]
}
```

Options per scanner: `backend_clients.client_target_map`,
`frontend_rest.base_class_service`, `ts_http_outbound.settings_key_map` /
`url_prefix_map` / `client_name_map`. Explicit values always win — the
auto-detector never overwrites what you wrote.
