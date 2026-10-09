import pytest

from costwatch.checks.ebs import old_snapshots, unattached_volumes
from tests.conftest import REGION


def test_unattached_volume_is_flagged_with_cost(session, ec2, config):
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=100, VolumeType="gp3")
    ec2.create_tags(Resources=[vol["VolumeId"]], Tags=[{"Key": "Name", "Value": "old-data"}])

    findings = unattached_volumes(session, REGION, config)

    assert [f.resource_id for f in findings] == [vol["VolumeId"]]
    assert findings[0].name == "old-data"
    assert findings[0].monthly_cost == pytest.approx(8.0)  # 100 GiB * $0.08


def test_attached_volume_is_not_flagged(session, ec2, config, ami_id):
    ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1)  # root volume is attached

    assert unattached_volumes(session, REGION, config) == []


def test_old_snapshot_is_flagged(session, ec2, future_config, preexisting_snapshots):
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=50)
    snap = ec2.create_snapshot(VolumeId=vol["VolumeId"])

    findings = [
        f
        for f in old_snapshots(session, REGION, future_config)
        if f.resource_id not in preexisting_snapshots
    ]

    assert [f.resource_id for f in findings] == [snap["SnapshotId"]]
    assert findings[0].monthly_cost == pytest.approx(2.5)  # 50 GiB * $0.05


def test_recent_snapshot_is_not_flagged(session, ec2, config):
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=50)
    snap = ec2.create_snapshot(VolumeId=vol["VolumeId"])

    ids = {f.resource_id for f in old_snapshots(session, REGION, config)}
    assert snap["SnapshotId"] not in ids


def test_snapshot_used_by_ami_is_not_flagged(session, ec2, future_config):
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=50)
    snap = ec2.create_snapshot(VolumeId=vol["VolumeId"])
    image_id = ec2.register_image(
        Name="my-ami",
        RootDeviceName="/dev/xvda",
        BlockDeviceMappings=[
            {"DeviceName": "/dev/xvda", "Ebs": {"SnapshotId": snap["SnapshotId"]}}
        ],
    )["ImageId"]
    # moto creates its own backing snapshot rather than using the one passed in
    image = ec2.describe_images(ImageIds=[image_id])["Images"][0]
    ami_snapshot = image["BlockDeviceMappings"][0]["Ebs"]["SnapshotId"]

    ids = {f.resource_id for f in old_snapshots(session, REGION, future_config)}
    assert ami_snapshot not in ids
