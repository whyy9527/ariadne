# Example: One Platform GraphQL Services

This example scans the `user-group-service` and `feedback-service` packages in
[1-Platform/one-platform](https://github.com/1-Platform/one-platform). It
exercises GraphQL SDL and TypeScript outbound HTTP scanning without running the
services.

The upstream repository is MIT licensed and pinned to
`4abf36c30380647e077c140096e715dd07e997a3`.

## Run it

```bash
git clone https://github.com/whyy9527/ariadne.git
cd ariadne
python -m pip install -e .
python examples/run.py one-platform
```

The runner scans the two packages, queries `feedback`, and checks the result
against reviewed GraphQL operation node IDs in `expected.json`.
