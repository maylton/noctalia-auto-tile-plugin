#!/usr/bin/env python3
from __future__ import annotations

import py_compile
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "catalog.toml"


def fail(message: str) -> None:
    raise SystemExit(f"validation error: {message}")


def main() -> None:
    with CATALOG.open("rb") as handle:
        catalog = tomllib.load(handle)

    entries = catalog.get("plugin")
    if not isinstance(entries, list) or not entries:
        fail("catalog.toml has no [[plugin]] entries")

    for row in entries:
        plugin_id = row.get("id")
        if not isinstance(plugin_id, str) or "/" not in plugin_id:
            fail(f"invalid plugin id: {plugin_id!r}")

        directory = ROOT / plugin_id.split("/", 1)[1]
        manifest_path = directory / "plugin.toml"
        if not manifest_path.is_file():
            fail(f"missing {manifest_path.relative_to(ROOT)}")

        with manifest_path.open("rb") as handle:
            manifest = tomllib.load(handle)

        for key in ("id", "name", "version", "author", "min_noctalia", "tags"):
            if key not in manifest:
                fail(f"{manifest_path.relative_to(ROOT)} is missing {key}")

        for key in ("id", "name", "version", "author", "min_noctalia"):
            if row.get(key) != manifest.get(key):
                fail(f"catalog {key} does not match {manifest_path.relative_to(ROOT)}")

        if row.get("tags") != manifest.get("tags"):
            fail(f"catalog tags do not match {manifest_path.relative_to(ROOT)}")

        for block_name in ("service", "widget", "shortcut", "launcher_provider", "desktop_widget"):
            for block in manifest.get(block_name, []):
                entry = block.get("entry")
                if not isinstance(entry, str) or not (directory / entry).is_file():
                    fail(f"missing entry file {entry!r} for {block_name}")

        daemon = directory / "auto-tile.py"
        if daemon.exists():
            py_compile.compile(str(daemon), doraise=True)

    print(f"Validated {len(entries)} plugin(s).")


if __name__ == "__main__":
    try:
        main()
    except (OSError, tomllib.TOMLDecodeError, py_compile.PyCompileError) as error:
        print(f"validation error: {error}", file=sys.stderr)
        raise SystemExit(1)
