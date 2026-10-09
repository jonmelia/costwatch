import pytest

from costwatch.checks.elb import idle_classic_load_balancers, idle_load_balancers
from tests.conftest import REGION, put_daily


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


def alb_dimension(arn):
    return arn.split(":loadbalancer/", 1)[1]


def test_alb_with_targets_but_no_traffic_is_flagged(
    session, ec2, subnets, cloudwatch, future_config, ami_id
):
    vpc_id, subnet_ids = subnets
    elbv2 = session.client("elbv2", region_name=REGION)
    lb = elbv2.create_load_balancer(Name="quiet-alb", Subnets=subnet_ids)["LoadBalancers"][0]
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

    findings = idle_load_balancers(session, REGION, future_config)

    assert [f.name for f in findings] == ["quiet-alb"]
    assert "no traffic in 14 days" in findings[0].description


def test_alb_without_targets_but_with_traffic_is_not_flagged(session, subnets, cloudwatch, config):
    # e.g. an ALB that only redirects HTTP to HTTPS
    _, subnet_ids = subnets
    elbv2 = session.client("elbv2", region_name=REGION)
    lb = elbv2.create_load_balancer(Name="redirector", Subnets=subnet_ids)["LoadBalancers"][0]
    put_daily(
        cloudwatch,
        "AWS/ApplicationELB",
        "RequestCount",
        {"LoadBalancer": alb_dimension(lb["LoadBalancerArn"])},
        [500.0] * 3,
        config.now,
    )

    assert idle_load_balancers(session, REGION, config) == []


def test_load_balancer_tags_are_collected(session, subnets, config):
    _, subnet_ids = subnets
    elbv2 = session.client("elbv2", region_name=REGION)
    elbv2.create_load_balancer(
        Name="tagged-alb", Subnets=subnet_ids, Tags=[{"Key": "Owner", "Value": "alice"}]
    )

    findings = idle_load_balancers(session, REGION, config)

    assert findings[0].tags == {"Owner": "alice"}


def test_classic_elb_with_instances_but_no_traffic_is_flagged(session, ec2, future_config, ami_id):
    elb = session.client("elb", region_name=REGION)
    elb.create_load_balancer(
        LoadBalancerName="quiet-clb",
        Listeners=[{"Protocol": "HTTP", "LoadBalancerPort": 80, "InstancePort": 80}],
        AvailabilityZones=[f"{REGION}a"],
    )
    instance = ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1)["Instances"][0]
    elb.register_instances_with_load_balancer(
        LoadBalancerName="quiet-clb", Instances=[{"InstanceId": instance["InstanceId"]}]
    )

    findings = idle_classic_load_balancers(session, REGION, future_config)

    assert [f.name for f in findings] == ["quiet-clb"]
    assert "no traffic" in findings[0].description


def test_new_classic_elb_with_instances_is_not_flagged(session, ec2, config, ami_id):
    elb = session.client("elb", region_name=REGION)
    elb.create_load_balancer(
        LoadBalancerName="new-clb",
        Listeners=[{"Protocol": "HTTP", "LoadBalancerPort": 80, "InstancePort": 80}],
        AvailabilityZones=[f"{REGION}a"],
    )
    instance = ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1)["Instances"][0]
    elb.register_instances_with_load_balancer(
        LoadBalancerName="new-clb", Instances=[{"InstanceId": instance["InstanceId"]}]
    )

    assert idle_classic_load_balancers(session, REGION, config) == []
