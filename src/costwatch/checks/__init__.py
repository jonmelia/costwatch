from dataclasses import dataclass

from costwatch.checks.ebs import gp2_volumes, old_snapshots, unattached_volumes
from costwatch.checks.ec2 import (
    idle_instances,
    long_stopped_instances,
    unused_amis,
    unused_elastic_ips,
)
from costwatch.checks.elb import idle_classic_load_balancers, idle_load_balancers
from costwatch.checks.logs import log_groups_without_retention
from costwatch.checks.network import idle_nat_gateways
from costwatch.checks.rds import (
    idle_rds_instances,
    old_rds_cluster_snapshots,
    old_rds_snapshots,
    retained_rds_backups,
)


@dataclass(frozen=True)
class CheckSpec:
    id: str  # matches Finding.check
    description: str
    functions: tuple


CHECKS = [
    CheckSpec(
        "unattached-ebs-volume", "EBS volumes not attached to any instance", (unattached_volumes,)
    ),
    CheckSpec(
        "old-ebs-snapshot",
        "EBS snapshots older than --snapshot-age-days not used by an AMI",
        (old_snapshots,),
    ),
    CheckSpec("gp2-volume", "Attached gp2 volumes that would be cheaper as gp3", (gp2_volumes,)),
    CheckSpec(
        "unused-elastic-ip", "Elastic IPs not associated with anything", (unused_elastic_ips,)
    ),
    CheckSpec(
        "long-stopped-instance",
        "Instances stopped longer than --stopped-days",
        (long_stopped_instances,),
    ),
    CheckSpec(
        "idle-ec2-instance",
        "Running instances with CPU never above 5% in --idle-days",
        (idle_instances,),
    ),
    CheckSpec("unused-ami", "Old AMIs not used by any instance or launch template", (unused_amis,)),
    CheckSpec(
        "idle-load-balancer",
        "Load balancers with no targets or no traffic",
        (idle_load_balancers, idle_classic_load_balancers),
    ),
    CheckSpec(
        "idle-nat-gateway",
        "NAT gateways with almost no traffic in --idle-days",
        (idle_nat_gateways,),
    ),
    CheckSpec(
        "old-rds-snapshot",
        "Manual RDS/Aurora snapshots older than --snapshot-age-days",
        (old_rds_snapshots, old_rds_cluster_snapshots),
    ),
    CheckSpec(
        "idle-rds-instance",
        "RDS instances with no connections in --idle-days",
        (idle_rds_instances,),
    ),
    CheckSpec(
        "retained-rds-backup",
        "Automated RDS backups kept after their instance was deleted",
        (retained_rds_backups,),
    ),
    CheckSpec(
        "log-group-no-retention",
        "Log groups over 1 GiB kept forever",
        (log_groups_without_retention,),
    ),
]

CHECK_IDS = [spec.id for spec in CHECKS]
ALL_CHECKS = [fn for spec in CHECKS for fn in spec.functions]


def select_checks(include: list[str] | None = None, exclude: list[str] | None = None) -> list:
    """Check functions for the given IDs. Raises ValueError for unknown IDs."""
    unknown = sorted((set(include or []) | set(exclude or [])) - set(CHECK_IDS))
    if unknown:
        raise ValueError(f"unknown check(s): {', '.join(unknown)}")
    return [
        fn
        for spec in CHECKS
        if (not include or spec.id in include) and spec.id not in (exclude or [])
        for fn in spec.functions
    ]
