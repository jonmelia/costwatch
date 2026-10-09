import importlib.util
from datetime import date
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "prepare_price_release", Path(__file__).parent.parent / "scripts" / "prepare_price_release.py"
)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)

CHANGELOG = """# Changelog

Intro.

## [1.0.1] - 2026-10-09

### Fixed
- Something.

[1.0.1]: https://github.com/jonmelia/costwatch/compare/v1.0.0...v1.0.1
"""


def test_adds_entry_and_link():
    out = prepare.add_entry(CHANGELOG, "1.0.2", "2026-10-15T18:00:00Z", date(2026, 10, 19))

    assert out.index("## [1.0.2] - 2026-10-19") < out.index("## [1.0.1]")
    assert "Prices updated from the AWS price list published 2026-10-15." in out
    assert out.index("[1.0.2]: https://github.com/jonmelia/costwatch/compare/v1.0.1...v1.0.2") < (
        out.index("[1.0.1]: ")
    )


def test_is_idempotent():
    once = prepare.add_entry(CHANGELOG, "1.0.2", "2026-10-15", date(2026, 10, 19))
    assert prepare.add_entry(once, "1.0.2", "2026-10-15", date(2026, 10, 19)) == once


def test_changelog_without_versions():
    out = prepare.add_entry("# Changelog\n", "0.0.1", "2026-10-15", date(2026, 10, 19))
    assert out.endswith("- Prices updated from the AWS price list published 2026-10-15.\n\n")
