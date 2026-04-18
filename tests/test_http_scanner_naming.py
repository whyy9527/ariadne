"""Regression tests pinned by real Spring samples (petclinic, plain-resource files).

Discovered empirically by running ariadne against
spring-petclinic-microservices — the old scanner missed *Resource.java files
entirely, and the regex swallowed trailing annotations (@ResponseStatus) as
part of the route path when @PostMapping had no parenthesised argument.
"""
from pathlib import Path
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ariadne_mcp.scanner.http_scanner import scan_http_controllers


PETCLINIC_OWNER_RESOURCE = """\
package org.springframework.samples.petclinic.customers.web;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/owners")
class OwnerResource {

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Owner createOwner(@Valid @RequestBody OwnerRequest req) {
        return null;
    }

    @GetMapping(value = "/{ownerId}")
    public Owner findOwner(@PathVariable int ownerId) {
        return null;
    }

    @PutMapping("/{ownerId}")
    public void updateOwner(@PathVariable int ownerId) { }
}
"""


def test_resource_filename_is_scanned(tmp_path: Path) -> None:
    (tmp_path / "OwnerResource.java").write_text(PETCLINIC_OWNER_RESOURCE)
    nodes = scan_http_controllers(str(tmp_path), "customers")
    # Previously: 0 — scanner only globbed *Controller.java / *Router.kt.
    assert len(nodes) == 3, f"expected 3 endpoints, got {[n['raw_name'] for n in nodes]}"


def test_postmapping_no_parens_then_annotation(tmp_path: Path) -> None:
    """@PostMapping with no args followed by @ResponseStatus(HttpStatus.CREATED)
    must NOT treat the following annotation as part of the route path."""
    (tmp_path / "OwnerResource.java").write_text(PETCLINIC_OWNER_RESOURCE)
    nodes = scan_http_controllers(str(tmp_path), "customers")
    create = next(n for n in nodes if n["raw_name"] == "createOwner")
    assert create["path"] == "/owners", (
        f"expected '/owners', got {create['path']!r} — "
        "regex is swallowing the next annotation as route text"
    )
    assert create["method"] == "POST"


def test_existing_controller_naming_still_works(tmp_path: Path) -> None:
    """Guard the original *Controller.java path so the naming expansion
    doesn't regress anything."""
    (tmp_path / "FooController.java").write_text("""\
@RestController
@RequestMapping("/foo")
class FooController {
    @GetMapping("/bar")
    public String bar() { return ""; }
}
""")
    nodes = scan_http_controllers(str(tmp_path), "svc")
    assert len(nodes) == 1
    assert nodes[0]["path"] == "/foo/bar"
