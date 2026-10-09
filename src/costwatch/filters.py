from pathlib import Path

from costwatch.models import Finding

IGNORE_TAG = "costwatch:ignore"
_FALSE = {"false", "no", "0", "off"}


def read_ignore_file(path: str) -> set[str]:
    """Resource IDs/ARNs/names, one per line; '#' starts a comment."""
    ids = set()
    for line in Path(path).expanduser().read_text().splitlines():
        if entry := line.split("#", 1)[0].strip():
            ids.add(entry)
    return ids


def is_ignored(finding: Finding, ids: set[str]) -> bool:
    if finding.resource_id in ids or (finding.name and finding.name in ids):
        return True
    value = finding.tags.get(IGNORE_TAG)
    return value is not None and value.strip().lower() not in _FALSE


def apply_ignores(findings: list[Finding], ids: set[str]) -> tuple[list[Finding], int]:
    kept = [f for f in findings if not is_ignored(f, ids)]
    return kept, len(findings) - len(kept)
