"""On-demand prices in USD per region, from AWS's public price list.

The data ships with the package (data/prices.json.gz) and is refreshed weekly by
scripts/update_prices.py. Regions missing from it fall back to us-east-1 prices, and prices
missing for a region fall back to the us-east-1 defaults below. Estimates rank waste; they don't
reproduce your bill (no discounts, Savings Plans or free tier).
"""

import gzip
import json
from functools import cache
from importlib.resources import files
from typing import Any

HOURS_PER_MONTH = 730
FALLBACK_REGION = "us-east-1"
PUBLIC_IPV4_HOUR = 0.005  # the same in every region

# us-east-1 values, used when a price is missing for a region
DEFAULTS: dict[str, Any] = {
    "ebs_gb_month": {"gp3": 0.08, "gp2": 0.10, "io1": 0.125, "io2": 0.125, "st1": 0.045,
                     "sc1": 0.015, "standard": 0.05},
    "ebs_iops_month": {"gp3": 0.005, "io1": 0.065, "io2": 0.065},
    "gp3_throughput_mibps_month": 0.04,
    "snapshot_gb_month": 0.05,
    "snapshot_archive_gb_month": 0.0125,
    "nat_gateway_hour": 0.045,
    "lb_hour": {"application": 0.0225, "network": 0.0225, "gateway": 0.0125, "classic": 0.025},
    "rds_storage_gb_month": {"gp2": 0.115, "gp3": 0.115, "io1": 0.125, "io2": 0.125,
                             "standard": 0.10},
    "rds_backup_gb_month": 0.095,
    "aurora_backup_gb_month": 0.021,
    "logs_gb_month": 0.03,
}  # fmt: skip

# RDS engine names as the API reports them -> as the price list names them
RDS_ENGINES = {
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "aurora-postgresql": "Aurora PostgreSQL",
    "aurora-mysql": "Aurora MySQL",
    "aurora": "Aurora MySQL",
}

_override: dict | None = None


@cache
def _bundled() -> dict:
    path = files("costwatch") / "data" / "prices.json.gz"
    return json.loads(gzip.decompress(path.read_bytes()))


def _data() -> dict:
    return _override if _override is not None else _bundled()


def use_data(data: dict | None) -> None:
    """Replace the price data (tests); None restores the bundled file."""
    global _override
    _override = data


def has_region(region: str) -> bool:
    return region in _data()["regions"]


def publication_date() -> str | None:
    return _data().get("publication_date")


def _region(region: str) -> dict:
    regions = _data()["regions"]
    return regions.get(region) or regions.get(FALLBACK_REGION, {})


def _rate(region: str, key: str, sub: str | None = None) -> float:
    value = _region(region).get(key)
    if sub is not None:
        value = (value or {}).get(sub)
        default = DEFAULTS[key].get(sub)
        if default is None:  # unknown subtype: price like the most common one
            default = next(iter(DEFAULTS[key].values()))
    else:
        default = DEFAULTS[key]
    return value if value is not None else default


def ebs_volume_monthly(
    region: str,
    volume_type: str,
    size_gb: int,
    iops: int | None = None,
    throughput: int | None = None,
) -> float:
    cost = size_gb * _rate(region, "ebs_gb_month", volume_type)
    if volume_type in ("io1", "io2") and iops:
        cost += iops * _rate(region, "ebs_iops_month", volume_type)
    if volume_type == "gp3":
        cost += max(0, (iops or 3000) - 3000) * _rate(region, "ebs_iops_month", "gp3")
        cost += max(0, (throughput or 125) - 125) * _rate(region, "gp3_throughput_mibps_month")
    return cost


def gp2_to_gp3_monthly_savings(region: str, size_gb: int) -> float:
    """Saving from moving gp2 to gp3 while matching gp2's baseline IOPS and throughput."""
    iops = min(max(100, 3 * size_gb), 16000)
    # gp2 only sustains 250 MB/s from 334 GiB; below that it's 128 MB/s plus short bursts
    throughput = 250 if size_gb >= 334 else 125
    gp3 = ebs_volume_monthly(region, "gp3", size_gb, iops=max(3000, iops), throughput=throughput)
    return ebs_volume_monthly(region, "gp2", size_gb) - gp3


def snapshot_monthly(region: str, size_gb: int, archived: bool = False) -> float:
    key = "snapshot_archive_gb_month" if archived else "snapshot_gb_month"
    return size_gb * _rate(region, key)


def rds_snapshot_monthly(region: str, size_gb: int, aurora: bool = False) -> float:
    key = "aurora_backup_gb_month" if aurora else "rds_backup_gb_month"
    return size_gb * _rate(region, key)


def rds_storage_monthly(
    region: str, storage_type: str, size_gb: int, multi_az: bool = False
) -> float:
    return size_gb * _rate(region, "rds_storage_gb_month", storage_type) * (2 if multi_az else 1)


def public_ipv4_monthly() -> float:
    return PUBLIC_IPV4_HOUR * HOURS_PER_MONTH


def load_balancer_monthly(region: str, lb_type: str) -> float:
    return _rate(region, "lb_hour", lb_type) * HOURS_PER_MONTH


def nat_gateway_monthly(region: str) -> float:
    return _rate(region, "nat_gateway_hour") * HOURS_PER_MONTH


def log_storage_monthly(region: str, stored_bytes: int) -> float:
    return stored_bytes / 2**30 * _rate(region, "logs_gb_month")


def ec2_instance_monthly(region: str, instance_type: str) -> float | None:
    """Linux on-demand. None when the type isn't sold in the region's price list."""
    hourly = _region(region).get("ec2_hour", {}).get(instance_type)
    return hourly * HOURS_PER_MONTH if hourly is not None else None


def rds_instance_monthly(
    region: str, instance_class: str, engine: str, multi_az: bool = False
) -> float | None:
    """None for engines or classes without a no-licence on-demand price (e.g. Oracle)."""
    engine_name = RDS_ENGINES.get(engine)
    prices = _region(region).get("rds_hour", {}).get(engine_name or "", {}).get(instance_class)
    if not prices:
        return None
    hourly = prices[1] if multi_az else prices[0]
    return hourly * HOURS_PER_MONTH if hourly is not None else None
