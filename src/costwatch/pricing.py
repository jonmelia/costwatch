"""Approximate on-demand prices in USD (us-east-1).

Other regions are typically within about 20% of these. They are estimates for
ranking waste, not billing figures.
"""

import re

HOURS_PER_MONTH = 730

EBS_GB_MONTH = {
    "gp3": 0.08,
    "gp2": 0.10,
    "io1": 0.125,
    "io2": 0.125,
    "st1": 0.045,
    "sc1": 0.015,
    "standard": 0.05,
}
IO_IOPS_MONTH = 0.065  # io1/io2 provisioned IOPS
GP3_IOPS_MONTH = 0.005  # above the 3,000 free baseline
GP3_THROUGHPUT_MONTH = 0.04  # per MB/s above the 125 MB/s free baseline

SNAPSHOT_GB_MONTH = 0.05
RDS_STORAGE_GB_MONTH = 0.115
LOGS_GB_MONTH = 0.03
NAT_GATEWAY_HOUR = 0.045
RDS_SNAPSHOT_GB_MONTH = 0.095
AURORA_SNAPSHOT_GB_MONTH = 0.021
PUBLIC_IPV4_HOUR = 0.005

LB_HOUR = {
    "application": 0.0225,
    "network": 0.0225,
    "gateway": 0.0125,
    "classic": 0.025,
}


def ebs_volume_monthly(
    volume_type: str, size_gb: int, iops: int | None = None, throughput: int | None = None
) -> float:
    cost = size_gb * EBS_GB_MONTH.get(volume_type, EBS_GB_MONTH["gp2"])
    if volume_type in ("io1", "io2") and iops:
        cost += iops * IO_IOPS_MONTH
    if volume_type == "gp3":
        cost += max(0, (iops or 3000) - 3000) * GP3_IOPS_MONTH
        cost += max(0, (throughput or 125) - 125) * GP3_THROUGHPUT_MONTH
    return cost


def snapshot_monthly(size_gb: int) -> float:
    return size_gb * SNAPSHOT_GB_MONTH


def rds_snapshot_monthly(size_gb: int, aurora: bool = False) -> float:
    return size_gb * (AURORA_SNAPSHOT_GB_MONTH if aurora else RDS_SNAPSHOT_GB_MONTH)


def public_ipv4_monthly() -> float:
    return PUBLIC_IPV4_HOUR * HOURS_PER_MONTH


def load_balancer_monthly(lb_type: str) -> float:
    return LB_HOUR.get(lb_type, LB_HOUR["application"]) * HOURS_PER_MONTH


def gp2_to_gp3_monthly_savings(size_gb: int) -> float:
    """Saving from moving gp2 to gp3 while matching gp2's baseline IOPS and throughput."""
    iops = min(max(100, 3 * size_gb), 16000)
    # gp2 only sustains 250 MB/s from 334 GiB; below that it's 128 MB/s plus short bursts
    throughput = 250 if size_gb >= 334 else 125
    gp3 = ebs_volume_monthly("gp3", size_gb, iops=max(3000, iops), throughput=throughput)
    return ebs_volume_monthly("gp2", size_gb) - gp3


def nat_gateway_monthly() -> float:
    return NAT_GATEWAY_HOUR * HOURS_PER_MONTH


def log_storage_monthly(stored_bytes: int) -> float:
    return stored_bytes / 2**30 * LOGS_GB_MONTH


# Linux on-demand $/hour for the .large size of each family; other sizes scale from it.
EC2_LARGE_HOUR = {
    "t2": 0.0928, "t3": 0.0832, "t3a": 0.0752, "t4g": 0.0672,
    "m4": 0.10, "m5": 0.096, "m5a": 0.086, "m6a": 0.0864, "m6g": 0.077, "m6i": 0.096,
    "m7g": 0.0816, "m7i": 0.1008,
    "c4": 0.10, "c5": 0.085, "c5a": 0.077, "c6g": 0.068, "c6i": 0.085, "c7g": 0.0725,
    "c7i": 0.08925,
    "r4": 0.133, "r5": 0.126, "r5a": 0.113, "r6g": 0.1008, "r6i": 0.126, "r7g": 0.1071,
    "r7i": 0.1323,
}  # fmt: skip

# Single-AZ MySQL/PostgreSQL $/hour for db.<family>.large
RDS_LARGE_HOUR = {
    "t3": 0.136, "t4g": 0.129,
    "m5": 0.171, "m6g": 0.152, "m6i": 0.171, "m7g": 0.168,
    "r5": 0.25, "r6g": 0.225, "r6i": 0.25, "r7g": 0.239,
}  # fmt: skip

_SIZE_FACTOR = {"nano": 1 / 16, "micro": 1 / 8, "small": 1 / 4, "medium": 1 / 2, "large": 1}


def _size_factor(size: str) -> float | None:
    if size in _SIZE_FACTOR:
        return _SIZE_FACTOR[size]
    if match := re.fullmatch(r"(\d*)xlarge", size):
        return 2 * int(match.group(1) or 1)
    return None


def _instance_hourly(instance_type: str, large_prices: dict[str, float]) -> float | None:
    family, _, size = instance_type.removeprefix("db.").partition(".")
    factor = _size_factor(size)
    if family not in large_prices or factor is None:
        return None
    return large_prices[family] * factor


def ec2_instance_monthly(instance_type: str) -> float | None:
    """None when the type isn't in the table."""
    hourly = _instance_hourly(instance_type, EC2_LARGE_HOUR)
    return hourly * HOURS_PER_MONTH if hourly is not None else None


def rds_instance_monthly(instance_class: str, multi_az: bool = False) -> float | None:
    hourly = _instance_hourly(instance_class, RDS_LARGE_HOUR)
    if hourly is None:
        return None
    return hourly * HOURS_PER_MONTH * (2 if multi_az else 1)
