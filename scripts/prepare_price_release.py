"""Add a CHANGELOG entry for a prices-only patch release.

Run after `uv version --bump patch`:

    uv run python scripts/prepare_price_release.py
"""

import gzip
import json
import re
import sys
import tomllib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
REPO = "https://github.com/jonmelia/costwatch"


def add_entry(changelog: str, version: str, prices_as_of: str, today: date) -> str:
    if f"## [{version}]" in changelog:
        return changelog  # already there, e.g. a re-run
    previous = re.search(r"^## \[([^\]]+)\]", changelog, re.M)
    entry = (
        f"## [{version}] - {today.isoformat()}\n\n"
        f"### Changed\n"
        f"- Prices updated from the AWS price list published {prices_as_of[:10]}.\n\n"
    )
    if previous is None:
        return changelog.rstrip("\n") + "\n\n" + entry
    changelog = changelog[: previous.start()] + entry + changelog[previous.start() :]
    link = f"[{version}]: {REPO}/compare/v{previous.group(1)}...v{version}\n"
    first_link = re.search(r"^\[[^\]]+\]: ", changelog, re.M)
    if first_link:
        changelog = changelog[: first_link.start()] + link + changelog[first_link.start() :]
    return changelog


def main() -> int:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    prices = json.loads(gzip.decompress((ROOT / "src/costwatch/data/prices.json.gz").read_bytes()))
    path = ROOT / "CHANGELOG.md"
    path.write_text(add_entry(path.read_text(), version, prices["publication_date"], date.today()))
    print(f"CHANGELOG entry for {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
