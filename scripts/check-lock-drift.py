#!/usr/bin/env python3
"""Compare two pip-compile lock files by pinned name==version lines only."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_LINE = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9._-]*)==([^;\s]+)")


def _pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line.strip())
        if m:
            pins[m.group(1).lower()] = m.group(2)
    return pins


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} LOCK_A LOCK_B", file=sys.stderr)
        return 2
    a, b = _pins(Path(argv[1])), _pins(Path(argv[2]))
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    diff_ver = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])
    if not only_a and not only_b and not diff_ver:
        print("lock pins OK")
        return 0
    if only_a:
        print("only in A:", only_a)
    if only_b:
        print("only in B:", only_b)
    if diff_ver:
        for k in diff_ver:
            print(f"version mismatch {k}: {a[k]} vs {b[k]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
