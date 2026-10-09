import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "compare_prices", Path(__file__).parent.parent / "scripts" / "compare_prices.py"
)
compare_prices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare_prices)


@pytest.fixture
def old():
    return {
        "us-east-1": {
            "ebs_gb_month": {"gp3": 0.08},
            "nat_gateway_hour": 0.045,
            "ec2_hour": {f"m5.{i}xlarge": 0.1 * i for i in range(1, 21)},
            "rds_hour": {},
        }
    }


def test_identical_is_safe(old):
    assert compare_prices.compare(old, copy.deepcopy(old)) == ([], [])


def test_small_price_change_is_safe(old):
    new = copy.deepcopy(old)
    new["us-east-1"]["ebs_gb_month"]["gp3"] = 0.084  # +5%
    new["us-east-1"]["ec2_hour"]["m5.1xlarge"] = 0.11

    problems, changes = compare_prices.compare(old, new)

    assert problems == []
    assert "us-east-1 ebs_gb_month.gp3: 0.08 → 0.084" in changes


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda r: r["ebs_gb_month"].update(gp3=0.8), "ebs_gb_month.gp3: 0.08 → 0.8"),
        (lambda r: r.pop("nat_gateway_hour"), "nat_gateway_hour: 0.045 → None"),
        (lambda r: [r["ec2_hour"].pop(f"m5.{i}xlarge") for i in range(1, 6)], "disappeared"),
        (lambda r: r["ec2_hour"].update({k: v * 3 for k, v in r["ec2_hour"].items()}), "moved"),
    ],
)
def test_suspicious_changes_need_review(old, mutate, expected):
    new = copy.deepcopy(old)
    mutate(new["us-east-1"])

    problems, _ = compare_prices.compare(old, new)

    assert any(expected in p for p in problems), problems


def test_missing_region_needs_review(old):
    problems, _ = compare_prices.compare(old, {})
    assert problems == ["us-east-1: region missing from the new prices"]
