# Custom scanners

Any language or framework not covered by the built-in scanners (see
[`CONFIG.md`](CONFIG.md#available-scanners)) can be added without
touching Ariadne's source code. Implement `scanner.BaseScanner`, put the
module somewhere Python can import it, and reference the class by dotted
path in `ariadne.config.json`:

```json
{
  "name": "my-go-service",
  "path": "../my-go-service",
  "scanners": [
    {
      "type": "my_scanners.go_scanner:GoRouteScanner",
      "route_file": "cmd/server/routes.go"
    }
  ]
}
```

`"type"` is `"module.path:ClassName"`. Every other key is passed to
`__init__`.

```python
# my_scanners/go_scanner.py
from scanner import BaseScanner

class GoRouteScanner(BaseScanner):
    def __init__(self, route_file: str = "routes.go"):
        self.route_file = route_file

    def scan(self, repo_path: str, service: str) -> list[dict]:
        # parse repo_path/self.route_file, return node dicts
        return [{
            "id": f"{service}::http::GET::/ping",
            "type": "http_endpoint",
            "raw_name": "ping",
            "service": service,
            "source_file": self.route_file,
            "method": "GET",
            "path": "/ping",
            "fields": [],
        }]
```

See `scanner/` in the repo for reference implementations of the built-in
scanners.
