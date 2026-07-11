from pathlib import Path

from ariadne_mcp.scanner.fastapi_scanner import scan_fastapi_routes


def test_scans_all_supported_methods_aliases_and_multiline_decorators(tmp_path: Path):
    source = '''
from fastapi import FastAPI as API, APIRouter as Router
import fastapi as fa

app = API()
router: Router = Router()
other = fa.APIRouter()

@app.get("/items/{item_id}")
async def get_item(item_id: str): pass

@router.post(
    path="/items/",
    status_code=201,
)
def create_item(): pass

@router.put("/items/{item_id}")
def put_item(): pass

@router.patch("/items/{item_id}")
def patch_item(): pass

@router.delete("/items/{item_id}")
def delete_item(): pass

@other.options("/items")
def options_items(): pass

@other.head("/health")
def head_health(): pass
'''
    (tmp_path / "routes.py").write_text(source, encoding="utf-8")
    nodes = scan_fastapi_routes(str(tmp_path), "catalog")

    assert [node["method"] for node in nodes] == [
        "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"
    ]
    assert nodes[0]["id"] == "catalog::http::GET::/items/{item_id}::get_item"
    assert nodes[0]["fields"] == ["item_id"]
    assert nodes[1]["path"] == "/items/"


def test_dynamic_path_is_skipped_without_execution(tmp_path: Path, capsys):
    source = '''
from fastapi import APIRouter
router = APIRouter()

def explode():
    raise RuntimeError("must never execute")

@router.get(explode())
def dynamic_route(): pass

@router.get("/safe")
def safe_route(): pass
'''
    (tmp_path / "routes.py").write_text(source, encoding="utf-8")
    nodes = scan_fastapi_routes(str(tmp_path), "svc")

    assert [node["raw_name"] for node in nodes] == ["safe_route"]
    assert "skipped dynamic route" in capsys.readouterr().err


def test_ignores_decorators_on_non_fastapi_objects(tmp_path: Path):
    (tmp_path / "routes.py").write_text(
        '''
class Fake:
    def get(self, path): return lambda function: function
fake = Fake()

@fake.get("/not-fastapi")
def fake_route(): pass
''',
        encoding="utf-8",
    )
    assert scan_fastapi_routes(str(tmp_path), "svc") == []


def test_requires_constructor_import_from_fastapi(tmp_path: Path):
    (tmp_path / "routes.py").write_text(
        '''
def APIRouter():
    return object()
router = APIRouter()

@router.get("/false-positive")
def local_route(): pass
''',
        encoding="utf-8",
    )
    assert scan_fastapi_routes(str(tmp_path), "svc") == []
