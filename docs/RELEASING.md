# Releasing Ariadne to PyPI

Single-maintainer flow. PyPI project name: `ariadne-mcp` (the short
`ariadne` is taken by the unrelated GraphQL server library). Install
path is `pip install ariadne-mcp`, import path is `ariadne_mcp`, CLI
is `ariadne-mcp`.

## Prerequisites (one-time)

1. PyPI account with 2FA enabled.
2. API token saved in your password manager. First release uses an
   account-scoped token (the project doesn't exist yet on PyPI); for
   subsequent releases, regenerate a project-scoped token for
   `ariadne-mcp` and use that.
3. `uv` installed locally. The build flow below uses `uv` with
   disposable venvs so nothing leaks into the user site-packages.

## Release steps

```bash
# 0. Sanity: clean tree, correct branch
git status                          # must be clean
git checkout main
git pull

# 1. Bump version
#    Edit pyproject.toml  ->  project.version
#    Edit ariadne_mcp/__init__.py  ->  __version__
#    Keep them in lockstep. Follow semver.

# 2. Run tests
uv run --python 3.12 --with pytest --with mcp pytest tests/

# 3. Clean previous artifacts
rm -rf dist/ build/ *.egg-info

# 4. Build sdist + wheel
uv run --python 3.12 --with build python -m build

# 5. Check the dist
uv run --python 3.12 --with twine twine check dist/*

# 6. Smoke-test the wheel in an isolated env
uv run --python 3.12 --with dist/ariadne_mcp-<VER>-py3-none-any.whl \
    --isolated --no-project ariadne-mcp --help

# 7. Upload
#    Export the token *in this shell only* — do not commit it anywhere.
export UV_PUBLISH_TOKEN='pypi-...your-token...'
uv publish dist/*

# 8. Tag + push
git tag v<VER>
git push origin main --tags

# 9. Create a GitHub release off the tag (optional but recommended)
gh release create v<VER> --generate-notes
```

## Post-release verification

```bash
# Fresh env, install from PyPI, smoke-test
uv run --python 3.12 --isolated --no-project --with ariadne-mcp==<VER> \
    ariadne-mcp --help

uv run --python 3.12 --isolated --no-project --with ariadne-mcp==<VER> \
    python -c "from ariadne_mcp.scanner import BaseScanner; print(BaseScanner)"
```

If this fails, yank the release (`pypi.org` → project → Manage → Yank)
and fix forward with a new patch version. **PyPI never lets you
re-upload the same version**, so don't reuse a version number to "try
again".

## Notes

- `mcp>=1.0` is the only runtime dep. The embedding-based recall layer
  (`onnxruntime`, `tokenizers`, `huggingface_hub`) is lazy-loaded and
  documented as an optional extra for power users; the core tool works
  on TF-IDF alone.
- Resource files (`claude-md-snippet.md`, `ariadne.config.example.json`)
  live inside the `ariadne_mcp/` package and are declared in
  `[tool.setuptools.package-data]`. If you add more resources, update
  that list, rebuild, and confirm they are present in the wheel's
  `RECORD`.
- The MCP server command written into `.mcp.json` by `ariadne-mcp
  install` uses `sys.executable -m ariadne_mcp.server`, so it picks up
  the installed package no matter where the user installed it (pipx,
  uv tool, system pip, venv).
