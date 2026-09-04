"""Migrate .env: RHTHS_API_KEY=xxx → FUYAO_API_KEY=xxx (preserve value)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_OLD_KEY = "RHTHS_API_KEY="
_NEW_KEY = "FUYAO_API_KEY="


def migrate(path: Path, dry_run: bool = False) -> int:
    if not path.exists():
        print(f"{path} not found; copy from .env.example first", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    has_old = any(line.startswith(_OLD_KEY) for line in lines)
    has_new = any(line.startswith(_NEW_KEY) for line in lines)

    if has_old and has_new:
        print("both vars present; resolve manually", file=sys.stderr)
        return 1
    if not has_old:
        if has_new:
            print("FUYAO_API_KEY already present, skipping", file=sys.stderr)
        else:
            print("nothing to migrate (no RHTHS_API_KEY or FUYAO_API_KEY present)", file=sys.stderr)
        return 0

    new_lines = [
        _NEW_KEY + line[len(_OLD_KEY):] if line.startswith(_OLD_KEY) else line
        for line in lines
    ]
    new_text = "".join(new_lines)

    if dry_run:
        print("would rename RHTHS_API_KEY → FUYAO_API_KEY (value redacted)")
        return 0

    bak = path.with_suffix(path.suffix + ".bak")
    if bak.exists():
        print(f"warning: {bak} already exists, overwriting", file=sys.stderr)
    bak.write_text(text, encoding="utf-8")
    path.write_text(new_text, encoding="utf-8")
    print(f"migrated {path} (backup: {bak})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--path", default=".env", help="path to .env file (default: ./.env)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    return migrate(Path(args.path), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
