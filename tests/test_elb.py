import pytest

from costwatch.checks.elb import idle_classic_load_balancers, idle_load_balancers
from tests.conftest import REGION


@pytest.fixture
def subnets(ec2):
    vpc_id = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    ids = [
        ec2.create_subnet(VpcId=vpc_id, CidrBlock=cidr, AvailabilityZone=f"{REGION}{az}")["Subnet"][
            "SubnetId"
        ]
        for cidr, az in (("10.0.1.0/24", "a"), ("10.0.2.0/24", "b"))
    ]
    return vpc_id, ids


def test_alb_without_targets_is_flagged(session, subnets, config):
    _, subnet_ids = subnets
    elbv2 = session.client("elbv2", region_name=REGION)
    lb = elbv2.create_load_balancer(Name="idle-alb", Subnets=subnet_ids)["LoadBalancers"][0]

    findings = idle_load_balancers(session, REGION, config)

    assert [f.resource_id for f in findings] == [lb["LoadBalancerArn"]]
    assert findings[0].monthly_cost == pytest.approx(16.425)


def test_alb_with_targets_is_not_flagged(session, ec2, subnets, config, ami_id):
    vpc_id, subnet_ids = subnets
    elbv2 = session.client("elbv2", region_name=REGION)
    lb = elbv2.create_load_balancer(Name="busy-alb", Subnets=subnet_ids)["LoadBalancers"][0]
    tg = elbv2.create_target_group(Name="tg", Protocol="HTTP", Port=80, VpcId=vpc_id)[
        "TargetGroups"
    ][0]
    elbv2.create_listener(
        LoadBalancerArn=lb["LoadBalancerArn"],
        Protocol="HTTP",
        Port=80,
        DefaultActions=[{"Type": "forward", "TargetGroupArn": tg["TargetGroupArn"]}],
    )
    instance = ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1, SubnetId=subnet_ids[0])[
        "Instances"
    ][0]
    elbv2.register_targets(
        TargetGroupArn=tg["TargetGroupArn"], Targets=[{"Id": instance["InstanceId"]}]
    )

    assert idle_load_balancers(session, REGION, config) == []


def test_classic_elb_without_instances_is_flagged(session, config):
    elb = session.client("elb", region_name=REGION)
    elb.create_load_balancer(
        LoadBalancerName="old-clb",
        Listeners=[{"Protocol": "HTTP", "LoadBalancerPort": 80, "InstancePort": 80}],
        AvailabilityZones=[f"{REGION}a"],
    )

    findings = idle_classic_load_balancers(session, REGION, config)

    assert [f.resource_id for f in findings] == ["old-clb"]
