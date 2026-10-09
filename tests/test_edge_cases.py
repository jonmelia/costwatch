"""Error paths and edge cases that the feature tests don't reach."""

import json
import runpy
import sys
from datetime import UTC, datetime

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from moto import mock_aws

from costwatch import cli, pricing
from costwatch.aws import short_error
from costwatch.checks.ebs import gp2_volumes
from costwatch.checks.ec2 import _stopped_since, idle_instances, unused_amis
from costwatch.checks.elb import idle_load_balancers
from costwatch.checks.rds import retained_rds_backups
from costwatch.cli import EXIT_ERROR, EXIT_OK, main
from costwatch.fmt import ago
from costwatch.iac import load_states
from costwatch.models import Finding
from costwatch.owners import identity
from costwatch.report import _resource_cell
from costwatch.scanner import enabled_regions
from tests.conftest import REGION, put_daily

# --- helpers and formatting ------------------------------------------------------------------


def test_short_error_for_botocore_errors():
    assert "Could not connect" in short_error(EndpointConnectionError(endpoint_url="https://x"))


@pytest.mark.parametrize(("days", "text"), [(0, "today"), (1, "1 day ago"), (5, "5 days ago")])
def test_ago(days, text):
    assert ago(days) == text


def test_identity_falls_back_to_username():
    event = {
        "Username": "fed-user",
        "CloudTrailEvent": json.dumps({"userIdentity": {"type": "FederatedUser"}}),
    }
    assert identity(event) == "fed-user"


def test_table_shows_name_not_arn():
    f = Finding("idle-load-balancer", REGION, "arn:aws:elb:x", "", 1.0, "", name="web")
    assert _resource_cell(f) == "web"


def test_stopped_since_without_a_timestamp():
    assert _stopped_since({"StateTransitionReason": ""}) is None
    assert _stopped_since(
        {"StateTransitionReason": "User initiated (2026-01-02 03:04:05 GMT)"}
    ) == (datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))


# --- pricing ----------------------------------------------------------------------------------


def test_bundled_prices_load():
    pricing.use_data(None)
    assert pricing.has_region("eu-west-1")
    assert pricing.publication_date()


def test_unknown_volume_type_is_priced_like_gp3():
    assert pricing.ebs_volume_monthly(REGION, "gp9", 100) == pytest.approx(8.0)


def test_provisioned_iops_volume():
    # 100 GiB io1 at $0.125 + 1,000 IOPS at $0.065
    assert pricing.ebs_volume_monthly(REGION, "io1", 100, iops=1000) == pytest.approx(77.5)


# --- checks -----------------------------------------------------------------------------------


def test_gp2_with_no_saving_is_skipped(session, ec2, config, ami_id):
    pricing._data()["regions"][REGION]["ebs_gb_month"]["gp3"] = 1.0  # gp3 dearer than gp2
    instance = ec2.run_instances(
        ImageId=ami_id, MinCount=1, MaxCount=1, Placement={"AvailabilityZone": f"{REGION}a"}
    )["Instances"][0]["InstanceId"]
    volume = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=100, VolumeType="gp2")
    ec2.attach_volume(VolumeId=volume["VolumeId"], InstanceId=instance, Device="/dev/sdf")

    assert gp2_volumes(session, REGION, config) == []


def test_idle_instance_price_notes(session, ec2, cloudwatch, future_config, ami_id):
    unpriced = ec2.run_instances(ImageId=ami_id, MinCount=1, MaxCount=1, InstanceType="c5.large")[
        "Instances"
    ][0]["InstanceId"]
    spot = ec2.run_instances(
        ImageId=ami_id,
        MinCount=1,
        MaxCount=1,
        InstanceType="m5.large",
        InstanceMarketOptions={"MarketType": "spot"},
    )["Instances"][0]["InstanceId"]
    for instance in (unpriced, spot):
        put_daily(
            cloudwatch, "AWS/EC2", "CPUUtilization", {"InstanceId": instance}, [1.0] * 14,
            future_config.now,
        )  # fmt: skip

    by_id = {f.resource_id: f for f in idle_instances(session, REGION, future_config)}

    assert "no on-demand Linux price" in by_id[unpriced].description
    assert by_id[unpriced].monthly_cost == 0.0
    assert "spot: on-demand price shown" in by_id[spot].description


def test_ami_used_by_launch_configuration_is_not_flagged(session, ec2, future_config):
    image = ec2.register_image(
        Name="asg-ami",
        RootDeviceName="/dev/xvda",
        BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 8}}],
    )["ImageId"]
    session.client("autoscaling", region_name=REGION).create_launch_configuration(
        LaunchConfigurationName="legacy", ImageId=image, InstanceType="t3.micro"
    )

    assert image not in {f.resource_id for f in unused_amis(session, REGION, future_config)}


def test_target_group_without_load_balancer_is_ignored(session, ec2, config):
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    elbv2 = session.client("elbv2", region_name=REGION)
    elbv2.create_target_group(Name="orphan-tg", Protocol="HTTP", Port=80, VpcId=vpc)
    subnets = [
        ec2.create_subnet(VpcId=vpc, CidrBlock=c, AvailabilityZone=f"{REGION}{z}")["Subnet"][
            "SubnetId"
        ]
        for c, z in (("10.0.1.0/24", "a"), ("10.0.2.0/24", "b"))
    ]
    elbv2.create_load_balancer(Name="lonely", Subnets=subnets)

    assert [f.name for f in idle_load_balancers(session, REGION, config)] == ["lonely"]


def test_active_automated_backups_are_not_flagged(session, config):
    rds = session.client("rds", region_name=REGION)
    rds.create_db_instance(
        DBInstanceIdentifier="live-db",
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        AllocatedStorage=20,
        MasterUsername="admin",
        MasterUserPassword="password123",
        BackupRetentionPeriod=7,
    )
    backups = rds.describe_db_instance_automated_backups()["DBInstanceAutomatedBackups"]
    assert backups and all(b["Status"] != "retained" for b in backups)

    assert retained_rds_backups(session, REGION, config) == []


def test_enabled_regions(session):
    regions = enabled_regions(session)
    assert REGION in regions and regions == sorted(regions)


# --- terraform state --------------------------------------------------------------------------


def test_empty_state_directory_and_prefix(session, tmp_path):
    s3 = session.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="tf-state")
    s3.put_object(Bucket="tf-state", Key="readme.txt", Body=b"no state here")

    _, errors = load_states(session, [str(tmp_path), "s3://tf-state/"])

    assert len(errors) == 2
    assert "no .tfstate files" in errors[0] and "no .tfstate objects" in errors[1]


# --- CLI --------------------------------------------------------------------------------------


@pytest.fixture
def volume():
    with mock_aws():
        boto3.client("ec2", region_name=REGION).create_volume(
            AvailabilityZone=f"{REGION}a", Size=10
        )
        yield


def test_table_to_stdout(volume, capsys):
    assert main(["scan", "--region", REGION]) == EXIT_OK
    assert "Total: ~$1.00/month" in capsys.readouterr().out  # 10 GiB gp2


def test_owners_flag(volume, capsys, monkeypatch):
    def fake_resolve(session, findings):
        for f in findings:
            f.owner, f.owner_source = "alice", "tag:Owner"
        return ["us-east-1 owner lookup: AccessDenied"]

    monkeypatch.setattr(cli, "resolve_owners", fake_resolve)

    assert main(["scan", "--region", REGION, "--owners", "--format", "json"]) == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["owners_checked"] is True
    assert report["findings"][0]["owner"] == "alice"
    assert report["errors"] == ["us-east-1 owner lookup: AccessDenied"]


def test_no_credentials(monkeypatch, tmp_path, capsys):
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    assert main(["scan", "--region", REGION]) == EXIT_ERROR
    assert "No AWS credentials found" in capsys.readouterr().err


def test_unknown_profile(capsys):
    assert main(["scan", "--profile", "does-not-exist"]) == EXIT_ERROR
    assert "could not be found" in capsys.readouterr().err


def test_missing_ignore_file(volume, tmp_path, capsys):
    missing = tmp_path / "nope.txt"
    assert main(["scan", "--region", REGION, "--ignore-file", str(missing)]) == EXIT_ERROR
    assert "nope.txt" in capsys.readouterr().err


def test_unwritable_output(volume, tmp_path, capsys):
    out = tmp_path / "missing-dir" / "report.json"
    assert main(["scan", "--region", REGION, "--format", "json", "-o", str(out)]) == EXIT_ERROR
    assert "Could not write report" in capsys.readouterr().err


@pytest.mark.parametrize("module", ["costwatch", "costwatch.cli"])
def test_python_dash_m(module, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["costwatch", "checks"])
    sys.modules.pop(module + ".__main__" if module == "costwatch" else module, None)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module(module, run_name="__main__")
    assert exit_info.value.code == EXIT_OK
    assert "unattached-ebs-volume" in capsys.readouterr().out
