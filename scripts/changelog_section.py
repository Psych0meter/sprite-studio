#!/usr/bin/env python3
"""Print one version's section of CHANGELOG.md (used to write release notes).

Usage: changelog_section.py VERSION [CHANGELOG]

VERSION is "X.Y.Z" (a leading "v" is accepted). The heading line is left
out; the section ends at the next "## " heading or at the link list at the
bottom. Exits with status 1 when the version has no section or it is empty.
"""

import re
import sys
from pathlib import Path


def section(text: str, version: str) -> str:
    version = version.removeprefix("v")
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = re.match(rf"## \[{re.escape(version)}\](\s|$)", line) is not None
            continue
        if inside:
            if re.match(r"\[[^\]]+\]: \S+", line):
                break
            out.append(line)
    return "\n".join(out).strip()


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[2]) if len(argv) == 3 else Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    body = section(path.read_text(encoding="utf-8"), argv[1])
    if not body:
        print(f"No changelog section for {argv[1]}", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
