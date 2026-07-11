#!/usr/bin/env python3
"""Prepare the pinned Petclinic checkout used by docs/demo.tape."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESTINATION = Path("/tmp/ariadne-demo-rec")
SAMPLE = DESTINATION / "spring-petclinic-microservices"


def main() -> None:
    metadata = json.loads(
        (ROOT / "examples" / "spring-petclinic" / "metadata.json").read_text(encoding="utf-8")
    )
    shutil.rmtree(DESTINATION, ignore_errors=True)
    DESTINATION.mkdir(parents=True)
    subprocess.run(["git", "init", str(SAMPLE)], check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git", "-C", str(SAMPLE), "fetch", "--depth", "1",
            metadata["repository"], metadata["revision"],
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(SAMPLE), "checkout", "--detach", "FETCH_HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )

    source_config = json.loads(
        (ROOT / "examples" / "spring-petclinic" / "ariadne.config.json").read_text(encoding="utf-8")
    )
    (DESTINATION / "ariadne.config.json").write_text(
        json.dumps(source_config, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
