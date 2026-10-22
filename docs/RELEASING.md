# Releasing Ariadne to PyPI

Single-maintainer flow. PyPI project name: `ariadne-mcp` (the short
`ariadne` is taken by the unrelated GraphQL server library). Install
path is `pip install ariadne-mcp`, import path is `ariadne_mcp`, CLI
is `ariadne-mcp`.

The supported Python versions are 3.10, 3.11, and 3.12. `uv.lock` pins
the maintainer environment; it does not replace the compatible dependency
ranges published for package users. Runtime dependencies are `mcp>=1,<2` and
`PyYAML>=6,<7`. `tiktoken` remains an optional `benchmark` extra, while pytest
and build tools are in the `dev` and `release` dependency groups.

## Prerequisites

- PyPI account with 2FA enabled.
- API token saved in a password manager. First release uses an account-scoped
  token; for later releases, use a project-scoped token for `ariadne-mcp`.
- `uv` 0.9.26. The project enforces this exact version.
- Git, `rg`, and `grep` available on `PATH`. Git is used for cloning and
  freshness checks; `rg` and `grep` are used only by the benchmark.

## Release steps

Start from an up-to-date `main` checkout and keep unrelated work out of the
release commit:

```bash
git status --short --branch
git switch main
git pull --ff-only
uv --version
VER=0.2.1
```

1. Set the release version in `pyproject.toml`, `ariadne_mcp/__init__.py`,
   and `server.json`'s top-level `version` and `packages[].version`.
   Follow semver, then regenerate `uv.lock` so its project version and
   dependency metadata stay synchronized:

   ```bash
   uv lock
   uv sync --locked --extra benchmark --group dev --group release
   ```

2. Review the exact release diff and verify the lock and version metadata before
   creating the release commit:

   ```bash
   git diff --check
   git diff -- pyproject.toml uv.lock ariadne_mcp/__init__.py server.json
   uv run --locked --extra benchmark --group dev --group release \
       python -m pytest tests/ -q
   ```

   Run the test command on Python 3.10, 3.11, and 3.12. CI uses that matrix
   and runs the same locked install and tests. The benchmark's first `tiktoken`
   encoding use downloads its encoding data, so that initial run needs network
   access.

3. Remove stale build outputs, then build both distributions from the locked
   environment and check their metadata:

   ```bash
   rm -rf dist/ build/
   find . -maxdepth 1 -name '*.egg-info' -exec rm -rf {} +
   uv run --locked --extra benchmark --group release \
       python -m build --no-isolation
   uv run --locked --extra benchmark --group release \
       python -m twine check dist/*
   ```

4. Smoke-test each built artifact in a temporary environment outside the
   checkout. Running from that temporary directory avoids importing source
   files from the repository instead of the installed distribution. This checks
   the wheel and sdist, CLI startup, and packaged resources:

   ```bash
   for artifact in "$PWD"/dist/*; do
     smoke_dir="$(mktemp -d)"
     uv venv "$smoke_dir/venv" --python 3.12
     uv pip install --python "$smoke_dir/venv/bin/python" "$artifact"
     (
       cd "$smoke_dir"
       "$smoke_dir/venv/bin/ariadne-mcp" --help
       "$smoke_dir/venv/bin/python" -c 'from importlib.resources import files; from ariadne_mcp.server import app; from ariadne_mcp.scanner.kafka_scanner import KafkaScanner; assert files("ariadne_mcp").joinpath("ariadne.config.example.json").is_file(); assert files("ariadne_mcp").joinpath("claude-md-snippet.md").is_file()'
     )
     rm -rf "$smoke_dir"
   done
   ```

5. Commit and review the exact release changes. Push the tested main commit
   and wait for all CI jobs, including the container smoke test, to pass before
   publishing. Keep unrelated edits and build outputs out of the commit:

   ```bash
   git status --short
   git show --check --oneline --stat HEAD
   git push origin main
   ```

6. Create and push the exact version tag, then upload the already checked
   artifacts. Export the token only in this shell; never commit it:

   ```bash
   git tag "v$VER"
   git push origin "v$VER"
   export UV_PUBLISH_TOKEN='pypi-...your-token...'
   uv publish dist/*
   ```

7. Create the GitHub release from that tag and attach both built files:

   ```bash
   gh release create "v$VER" \
       "dist/ariadne_mcp-$VER-py3-none-any.whl" \
       "dist/ariadne_mcp-$VER.tar.gz" \
       --generate-notes
   ```

8. After PyPI serves the new package and README, validate and publish to the
   Official MCP Registry:

   ```bash
   mcp-publisher validate
   mcp-publisher login github
   mcp-publisher publish
   ```

## Post-release verification

Install from PyPI in fresh environments and check both the CLI and imports:

```bash
uv run --python 3.12 --isolated --no-project \
    --with "ariadne-mcp==$VER" ariadne-mcp --help

uv run --python 3.12 --isolated --no-project \
    --with "ariadne-mcp==$VER" python -c \
    "from ariadne_mcp.scanner import BaseScanner; from ariadne_mcp.scanner.kafka_scanner import KafkaScanner; print(BaseScanner, KafkaScanner)"
```

If verification fails, yank the release in the PyPI project settings and fix
forward with a new patch version. **PyPI never lets you re-upload the same
version**, so do not reuse a version number.

## Distribution notes

- Package metadata declares compatible dependency ranges. `uv.lock` pins the
  repository's maintainer and CI environment; it is not a runtime lock imposed
  on package users.
- `mcp>=1,<2` is the MCP Python SDK v1 API range. `PyYAML>=6,<7` is required
  for Kafka YAML contract parsing. `tiktoken>=0.7` is optional and used only
  by benchmark tooling.
- Package resources such as `claude-md-snippet.md` and
  `ariadne.config.example.json` are declared in
  `[tool.setuptools.package-data]`. The artifact smoke test checks they are
  present after installing both wheel and sdist.
- The MCP server command written into `.mcp.json` by `ariadne-mcp install`
  uses `sys.executable -m ariadne_mcp.server`, so it uses the installed package
  whether installed with pipx, uv tool, system pip, or a virtual environment.
