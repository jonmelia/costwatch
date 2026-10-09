"""The published IAM policy must allow every AWS call costwatch makes.

moto doesn't enforce IAM, so this records each API call during a scan that exercises every
check and code path, and compares them with the policy.
"""

import contextlib
import json

from costwatch.checks import ALL_CHECKS
from costwatch.iam import POLICY, policy_json
from costwatch.owners import resolve_owners
from costwatch.scanner import scan
from tests.conftest import REGION

# Botocore signing names that differ from IAM action prefixes
_IAM_PREFIX = {"monitoring": "cloudwatch"}


def _seed(session, ec2, ami_id):
    """At least one resource of each kind, so every check reaches all of its API calls."""
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnets = [
        ec2.create_subnet(VpcId=vpc, CidrBlock=c, AvailabilityZone=f"{REGION}{z}")["Subnet"][
            "SubnetId"
        ]
        for c, z in (("10.0.1.0/24", "a"), ("10.0.2.0/24", "b"))
    ]
    running = ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1, SubnetId=subnets[0])[
        "Instances"
    ][0]["InstanceId"]
    stopped = ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1)["Instances"][0][
        "InstanceId"
    ]
    ec2.stop_instances(InstanceIds=[stopped])
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10)
    ec2.create_launch_template(LaunchTemplateName="lt", LaunchTemplateData={"ImageId": ami_id})
    ec2.register_image(
        Name="old",
        RootDeviceName="/dev/xvda",
        BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 8}}],
    )
    eip = ec2.allocate_address(Domain="vpc")["AllocationId"]
    ec2.create_nat_gateway(SubnetId=subnets[0], AllocationId=eip)
    ec2.allocate_address(Domain="vpc")

    elbv2 = session.client("elbv2", region_name=REGION)
    lb = elbv2.create_load_balancer(Name="alb", Subnets=subnets)["LoadBalancers"][0]
    tg = elbv2.create_target_group(Name="tg", Protocol="HTTP", Port=80, VpcId=vpc)["TargetGroups"][
        0
    ]
    elbv2.create_listener(
        LoadBalancerArn=lb["LoadBalancerArn"],
        Protocol="HTTP",
        Port=80,
        DefaultActions=[{"Type": "forward", "TargetGroupArn": tg["TargetGroupArn"]}],
    )
    elbv2.register_targets(TargetGroupArn=tg["TargetGroupArn"], Targets=[{"Id": running}])
    session.client("elb", region_name=REGION).create_load_balancer(
        LoadBalancerName="clb",
        Listeners=[{"Protocol": "HTTP", "LoadBalancerPort": 80, "InstancePort": 80}],
        AvailabilityZones=[f"{REGION}a"],
    )
    rds = session.client("rds", region_name=REGION)
    rds.create_db_instance(
        DBInstanceIdentifier="db",
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        AllocatedStorage=20,
        MasterUsername="admin",
        MasterUserPassword="password123",
    )
    session.client("logs", region_name=REGION).create_log_group(logGroupName="/app")


def test_policy_allows_every_call(session, ec2, future_config, ami_id):
    _seed(session, ec2, ami_id)
    calls: set[str] = set()

    def record(model, **_kwargs):
        prefix = model.service_model.signing_name
        calls.add(f"{_IAM_PREFIX.get(prefix, prefix)}:{model.name}")

    session.events.register("before-call", record)
    result = scan(session, regions=[REGION], config=future_config, checks=ALL_CHECKS)
    with contextlib.suppress(NotImplementedError):  # moto has no CloudTrail LookupEvents
        resolve_owners(session, result.findings)

    allowed = set(POLICY["Statement"][0]["Action"])
    assert calls, "no calls recorded"
    assert "cloudtrail:LookupEvents" in calls
    assert calls - allowed == set(), f"missing from the IAM policy: {sorted(calls - allowed)}"


def test_policy_is_read_only():
    for action in POLICY["Statement"][0]["Action"]:
        verb = action.split(":", 1)[1]
        assert verb.startswith(("Describe", "Get", "List", "Lookup")), action
    assert json.loads(policy_json()) == POLICY
