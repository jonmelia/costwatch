"""Compare two price files and decide whether a refresh is safe to merge automatically.

    uv run python scripts/compare_prices.py OLD.json.gz NEW.json.gz

Prints a Markdown summary. Exits 0 if the change looks like normal AWS price movement, 1 if
something should be looked at by a person: a core price moving more than MAX_CHANGE, a region
disappearing, or many instance types vanishing or repricing at once. Those usually mean the
price list format changed, not that AWS changed prices.
"""

import gzip
import json
import sys
from pathlib import Path

MAX_CHANGE = 0.25  # AWS list prices rarely move this much in a week
MAX_INSTANCE_LOSS = 0.10  # share of a region's instance types that may disappear
MAX_INSTANCE_REPRICED = 0.10  # share that may move by more than MAX_CHANGE


def load(path: str) -> dict:
    return json.loads(gzip.decompress(Path(path).read_bytes()))["regions"]


def _scalars(prices: dict) -> dict[str, float]:
    """Flatten the non-instance prices to {"ebs_gb_month.gp3": 0.08, ...}."""
    flat = {}
    for key, value in prices.items():
        if key in ("ec2_hour", "rds_hour"):
            continue
        if isinstance(value, dict):
            flat.update({f"{key}.{sub}": v for sub, v in value.items()})
        else:
            flat[key] = value
    return flat


def _moved(old: float, new: float) -> bool:
    return old > 0 and abs(new - old) / old > MAX_CHANGE


def compare(old: dict, new: dict) -> tuple[list[str], list[str]]:
    """Returns (problems, normal changes)."""
    problems, changes = [], []
    for region in sorted(set(old) - set(new)):
        problems.append(f"{region}: region missing from the new prices")
    for region in sorted(set(new) - set(old)):
        changes.append(f"{region}: new region")

    for region in sorted(set(old) & set(new)):
        before, after = _scalars(old[region]), _scalars(new[region])
        for key in sorted(before.keys() | after.keys()):
            a, b = before.get(key), after.get(key)
            if a == b:
                continue
            line = f"{region} {key}: {a} → {b}"
            if a is None:
                changes.append(line)
            elif b is None or _moved(a, b):
                problems.append(line)
            else:
                changes.append(line)

        old_ec2, new_ec2 = old[region]["ec2_hour"], new[region]["ec2_hour"]
        lost = set(old_ec2) - set(new_ec2)
        repriced = [t for t in set(old_ec2) & set(new_ec2) if _moved(old_ec2[t], new_ec2[t])]
        if len(lost) > MAX_INSTANCE_LOSS * len(old_ec2):
            problems.append(f"{region}: {len(lost)} of {len(old_ec2)} instance types disappeared")
        elif lost:
            changes.append(f"{region}: {len(lost)} instance type(s) retired")
        if len(repriced) > MAX_INSTANCE_REPRICED * len(old_ec2):
            problems.append(f"{region}: {len(repriced)} instance types moved over {MAX_CHANGE:.0%}")
        changes += [
            f"{region} ec2 {t}: {old_ec2[t]} → {new_ec2[t]}"
            for t in sorted(set(old_ec2) & set(new_ec2))
            if old_ec2[t] != new_ec2[t]
        ][:20]
    return problems, changes


def main() -> int:
    old, new = load(sys.argv[1]), load(sys.argv[2])
    problems, changes = compare(old, new)
    if problems:
        print("### ⚠️ Needs review: unusually large price changes\n")
        print("\n".join(f"- {p}" for p in problems[:50]))
        print()
    print(f"### Changes ({len(changes)})\n")
    print("\n".join(f"- {c}" for c in changes[:100]) or "- none")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
