from costwatch.checks.ebs import old_snapshots, unattached_volumes
from costwatch.checks.ec2 import long_stopped_instances, unused_elastic_ips
from costwatch.checks.elb import idle_classic_load_balancers, idle_load_balancers

ALL_CHECKS = [
    unattached_volumes,
    old_snapshots,
    unused_elastic_ips,
    long_stopped_instances,
    idle_load_balancers,
    idle_classic_load_balancers,
]
