import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_versions_and_registry_metadata_are_in_lockstep():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    module_version = (ROOT / "ariadne_mcp" / "__init__.py").read_text(encoding="utf-8")
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

    package_version = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE).group(1)
    assert f'__version__ = "{package_version}"' in module_version
    assert server["version"] == package_version
    assert server["packages"] == [
        {
            "registryType": "pypi",
            "identifier": "ariadne-mcp",
            "version": package_version,
            "transport": {"type": "stdio"},
        }
    ]


def test_pypi_readme_contains_matching_registry_ownership_marker():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert f"mcp-name: {server['name']} -->" in readme
    assert server["name"] == "io.github.whyy9527/ariadne"
