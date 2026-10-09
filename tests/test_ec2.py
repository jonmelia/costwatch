import pytest

from costwatch.checks.ec2 import long_stopped_instances, unused_elastic_ips
from tests.conftest import REGION


def test_unassociated_elastic_ip_is_flagged(session, ec2, config):
    alloc = ec2.allocate_address(Domain="vpc")

    findings = unused_elastic_ips(session, REGION, config)

    assert [f.resource_id for f in findings] == [alloc["AllocationId"]]
    assert findings[0].monthly_cost == pytest.approx(3.65)


def test_associated_elastic_ip_is_not_flagged(session, ec2, config, ami_id):
    instance = ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1)["Instances"][0]
    alloc = ec2.allocate_address(Domain="vpc")
    ec2.associate_address(AllocationId=alloc["AllocationId"], InstanceId=instance["InstanceId"])

    assert unused_elastic_ips(session, REGION, config) == []


def test_long_stopped_instance_is_flagged_with_volume_cost(session, ec2, future_config, ami_id):
    instance = ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1)["Instances"][0]
    ec2.stop_instances(InstanceIds=[instance["InstanceId"]])

    findings = long_stopped_instances(session, REGION, future_config)

    assert [f.resource_id for f in findings] == [instance["InstanceId"]]
    assert findings[0].monthly_cost > 0


def test_recently_stopped_instance_is_not_flagged(session, ec2, config, ami_id):
    instance = ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1)["Instances"][0]
    ec2.stop_instances(InstanceIds=[instance["InstanceId"]])

    assert long_stopped_instances(session, REGION, config) == []


def test_running_instance_is_not_flagged(session, ec2, future_config, ami_id):
    ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1)

    assert long_stopped_instances(session, REGION, future_config) == []
