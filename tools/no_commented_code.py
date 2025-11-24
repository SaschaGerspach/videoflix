"""Fail fast when commented-out code or German umlauts appear in tracked files.

The heuristic intentionally targets obvious code fragments that were commented
out (def/class/import/flow keywords) and comments that still contain umlauts.
"""

from __future__ import annotations

import pathlib
import re
import sys
from collections.abc import Iterable

CODEY = re.compile(
    r"^\s*(#|//)\s*(def|class|from\s+\S+\s+import|import\s+\S+|if\b|for\b|while\b|try\b|except\b|with\b|return\b|print\(|assert\b)"
)
UMLAUT = re.compile(r"[äöüÄÖÜß]")


def iter_violations(paths: Iterable[str]):
    for path_str in paths:
        path = pathlib.Path(path_str)
        if not path.exists() or path.is_dir():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for idx, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith(("#", "//")):
                continue
            if CODEY.search(line) or UMLAUT.search(line):
                yield f"{path}:{idx}: commented-out code or non-English comment -> {stripped}"


def main(argv: list[str]) -> int:
    violations = list(iter_violations(argv[1:]))
    if violations:
        sys.stderr.write("\n".join(violations) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
