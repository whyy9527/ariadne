# Example: FastAPI Users Microservice

This example scans the `users` service in
[Kludex/fastapi-microservices](https://github.com/Kludex/fastapi-microservices)
with Ariadne's standard-library AST scanner. The upstream repository is MIT
licensed and pinned to `262bd1b7a97d6a6375067abac778bb8d75bb5edc`.

## Run it

```bash
git clone https://github.com/whyy9527/ariadne.git
cd ariadne
python -m pip install -e .
python examples/run.py fastapi-microservices
```

The runner auto-detects FastAPI from `requirements.txt`, extracts literal
`APIRouter` decorators without importing the application, queries `user_id`,
and verifies the reviewed GET/DELETE route nodes in `expected.json`.

Router-prefix composition is outside this scanner's first static slice, so the
nodes preserve the literal decorator paths declared in each source file.
