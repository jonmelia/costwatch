"""Approximate on-demand prices in USD (us-east-1).

Other regions are typically within about 20% of these. They are estimates for
ranking waste, not billing figures.
"""

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
