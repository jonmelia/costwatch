import pytest

from costwatch.checks.ec2 import idle_instances, unused_amis
from costwatch.checks.network import idle_nat_gateways
from costwatch.checks.rds import idle_rds_instances, retained_rds_backups
from tests.conftest import REGION, put_daily


@pytest.fixture
def rds(session):
    return session.client("rds", region_name=REGION)


def launch(ec2, ami_id, instance_type="m5.large"):
    return ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1, InstanceType=instance_type)[
        "Instances"
    ][0]["InstanceId"]


def test_idle_ec2_instance(session, ec2, cloudwatch, future_config, ami_id):
    idle, busy, no_data = (launch(ec2, ami_id) for _ in range(3))
    put_daily(
        cloudwatch, "AWS/EC2", "CPUUtilization", {"InstanceId": idle}, [1.5] * 14, future_config.now
    )
    put_daily(
        cloudwatch,
        "AWS/EC2",
        "CPUUtilization",
        {"InstanceId": busy},
        [1.0] * 13 + [60.0],
        future_config.now,
    )

    findings = idle_instances(session, REGION, future_config)

    assert [f.resource_id for f in findings] == [idle]
    assert findings[0].monthly_cost == pytest.approx(0.096 * 730)
    assert "peak CPU 1.5%" in findings[0].description


def test_new_instance_is_not_idle(session, ec2, cloudwatch, config, ami_id):
    instance = launch(ec2, ami_id)
    put_daily(
        cloudwatch, "AWS/EC2", "CPUUtilization", {"InstanceId": instance}, [1.0] * 14, config.now
    )

    assert idle_instances(session, REGION, config) == []


def test_idle_nat_gateway(session, ec2, cloudwatch, future_config):
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.1.0/24")["Subnet"]["SubnetId"]

    def nat():
        eip = ec2.allocate_address(Domain="vpc")["AllocationId"]
        return ec2.create_nat_gateway(SubnetId=subnet, AllocationId=eip)["NatGateway"][
            "NatGatewayId"
        ]

    idle, busy = nat(), nat()
    metric = ("AWS/NATGateway", "BytesOutToDestination")
    put_daily(cloudwatch, *metric, {"NatGatewayId": idle}, [0.0] * 14, future_config.now)
    put_daily(cloudwatch, *metric, {"NatGatewayId": busy}, [5e9] * 14, future_config.now)

    findings = idle_nat_gateways(session, REGION, future_config)

    assert [f.resource_id for f in findings] == [idle]
    assert findings[0].monthly_cost == pytest.approx(32.85)


def test_idle_rds_instance(session, rds, cloudwatch, future_config):
    for name in ("idle-db", "busy-db"):
        rds.create_db_instance(
            DBInstanceIdentifier=name,
            DBInstanceClass="db.t3.micro",
            Engine="postgres",
            AllocatedStorage=20,
            MasterUsername="admin",
            MasterUserPassword="password123",
        )
    metric = ("AWS/RDS", "DatabaseConnections")
    put_daily(
        cloudwatch, *metric, {"DBInstanceIdentifier": "idle-db"}, [0.0] * 14, future_config.now
    )
    put_daily(
        cloudwatch,
        *metric,
        {"DBInstanceIdentifier": "busy-db"},
        [0.0] * 13 + [3.0],
        future_config.now,
    )

    findings = idle_rds_instances(session, REGION, future_config)

    assert [f.name for f in findings] == ["idle-db"]
    assert findings[0].monthly_cost == pytest.approx(0.018 * 730 + 20 * 0.115)


def test_retained_rds_backups(session, rds, config):
    rds.create_db_instance(
        DBInstanceIdentifier="old-db",
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        AllocatedStorage=20,
        MasterUsername="admin",
        MasterUserPassword="password123",
        BackupRetentionPeriod=7,
    )
    rds.delete_db_instance(
        DBInstanceIdentifier="old-db", SkipFinalSnapshot=True, DeleteAutomatedBackups=False
    )

    findings = retained_rds_backups(session, REGION, config)

    assert [f.name for f in findings] == ["old-db"]
    assert findings[0].check == "retained-rds-backup"


def register_ami(ec2, name):
    return ec2.register_image(
        Name=name,
        RootDeviceName="/dev/xvda",
        BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 30}}],
    )["ImageId"]


def test_unused_ami(session, ec2, future_config):
    unused = register_ami(ec2, "unused-ami")
    on_instance = register_ami(ec2, "instance-ami")
    in_template = register_ami(ec2, "template-ami")
    ec2.run_instances(ImageId=on_instance, MinCount=1, MaxCount=1)
    ec2.create_launch_template(
        LaunchTemplateName="web", LaunchTemplateData={"ImageId": in_template}
    )

    findings = unused_amis(session, REGION, future_config)

    assert [f.resource_id for f in findings] == [unused]
    assert findings[0].monthly_cost > 0


def test_recent_ami_is_not_flagged(session, ec2, config):
    register_ami(ec2, "fresh-ami")

    assert unused_amis(session, REGION, config) == []
