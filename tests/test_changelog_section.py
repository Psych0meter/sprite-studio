import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("changelog_section", ROOT / "scripts" / "changelog_section.py")
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)

TEXT = """# Changelog

## [Unreleased]

## [0.2.0] - 2026-12-01

### Fixed

- **Jobs**: a fix.

## [0.1.0] - 2026-11-01

First release.

### Added

- **Models**: things.

[Unreleased]: https://example.org/compare/v0.2.0...HEAD
[0.1.0]: https://example.org/releases/tag/v0.1.0
"""


def test_middle_section():
    assert cs.section(TEXT, "0.2.0") == "### Fixed\n\n- **Jobs**: a fix."


def test_last_section_stops_at_links():
    body = cs.section(TEXT, "v0.1.0")
    assert body.startswith("First release.")
    assert body.endswith("- **Models**: things.")


def test_missing_or_empty():
    assert cs.section(TEXT, "9.9.9") == ""
    assert cs.section(TEXT, "Unreleased") == ""


def test_real_changelog_has_current_version():
    version = (ROOT / "VERSION").read_text().strip()
    assert cs.section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
