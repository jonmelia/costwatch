import pytest

from costwatch.checks.ebs import gp2_volumes
from tests.conftest import REGION


def attached_volume(ec2, ami_id, volume_type, size=100):
    instance = ec2.run_instances(
        ImageId=ami_id, MinCount=1, MaxCount=1, Placement={"AvailabilityZone": f"{REGION}a"}
    )["Instances"][0]["InstanceId"]
    volume = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=size, VolumeType=volume_type)[
        "VolumeId"
    ]
    ec2.attach_volume(VolumeId=volume, InstanceId=instance, Device="/dev/sdf")
    return volume


def test_attached_gp2_volume_shows_saving(session, ec2, config, ami_id):
    gp2 = attached_volume(ec2, ami_id, "gp2")
    attached_volume(ec2, ami_id, "gp3")
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=100, VolumeType="gp2")  # unattached

    findings = [f for f in gp2_volumes(session, REGION, config) if f.resource_id == gp2]
    others = [f for f in gp2_volumes(session, REGION, config) if f.resource_id != gp2]

    assert len(findings) == 1
    assert findings[0].monthly_cost == pytest.approx(2.0)
    # Only root volumes moto creates for instances may remain; never the gp3 or unattached one
    assert all("gp2" in f.description for f in others)


def test_gp2_on_long_stopped_instance_is_not_double_counted(session, ec2, future_config, ami_id):
    from costwatch.checks.ec2 import long_stopped_instances

    instance = ec2.run_instances(
        ImageId=ami_id, MinCount=1, MaxCount=1, Placement={"AvailabilityZone": f"{REGION}a"}
    )["Instances"][0]["InstanceId"]
    volume = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=100, VolumeType="gp2")[
        "VolumeId"
    ]
    ec2.attach_volume(VolumeId=volume, InstanceId=instance, Device="/dev/sdf")
    ec2.stop_instances(InstanceIds=[instance])

    assert volume not in {f.resource_id for f in gp2_volumes(session, REGION, future_config)}
    # ...because the stopped instance's finding already includes it in full
    stopped = long_stopped_instances(session, REGION, future_config)
    assert stopped[0].monthly_cost >= 10.0  # 100 GiB gp2 at $0.10
