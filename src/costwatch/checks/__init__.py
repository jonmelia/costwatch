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

ALL_CHECKS = [
    unattached_volumes,
    old_snapshots,
    gp2_volumes,
    unused_elastic_ips,
    long_stopped_instances,
    idle_instances,
    unused_amis,
    idle_load_balancers,
    idle_classic_load_balancers,
    idle_nat_gateways,
    old_rds_snapshots,
    old_rds_cluster_snapshots,
    idle_rds_instances,
    retained_rds_backups,
    log_groups_without_retention,
]
