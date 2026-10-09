import json

import boto3
import pytest
from moto import mock_aws

from costwatch.cli import main
from costwatch.iac import StateIndex, annotate, load_states
from costwatch.models import Finding
from tests.conftest import REGION


def state(*resources):
    return {"version": 4, "terraform_version": "1.9.0", "resources": list(resources)}


def resource(type_, name, *instances, module=None, mode="managed"):
    r = {"mode": mode, "type": type_, "name": name, "instances": list(instances)}
    if module:
        r["module"] = module
    return r


def instance(attributes, **extra):
    return {"attributes": attributes, **extra}


def finding(check, resource_id, name=None, tags=None):
    return Finding(
        check=check,
        region=REGION,
        resource_id=resource_id,
        description="",
        monthly_cost=1.0,
        recommendation="delete it",
        name=name,
        tags=tags or {},
    )


@pytest.fixture
def index():
    idx = StateIndex()
    idx.add_state(
        state(
            resource("aws_ebs_volume", "data", instance({"id": "vol-data", "arn": "arn:vol"})),
            resource(
                "aws_instance",
                "web",
                instance(
                    {
                        "id": "i-web",
                        "root_block_device": [{"volume_id": "vol-root"}],
                        "ebs_block_device": [{"volume_id": "vol-extra"}],
                    },
                    index_key=0,
                ),
            ),
            resource(
                "aws_nat_gateway",
                "this",
                instance({"id": "nat-1"}, index_key="eu-west-1a"),
                module="module.vpc",
            ),
            # Data sources only read existing resources; they don't own them
            resource("aws_ebs_volume", "lookup", instance({"id": "vol-looked-up"}), mode="data"),
            resource(
                "aws_db_snapshot",
                "pre",
                instance({"id": "pre-upgrade", "db_snapshot_arn": "arn:snap"}),
            ),
        ),
        "prod.tfstate",
    )
    return idx


@pytest.mark.parametrize(
    ("f", "address"),
    [
        (finding("unattached-ebs-volume", "vol-data"), "aws_ebs_volume.data"),
        (finding("gp2-volume", "vol-root"), "aws_instance.web[0]"),
        (finding("gp2-volume", "vol-extra"), "aws_instance.web[0]"),
        (finding("idle-ec2-instance", "i-web"), "aws_instance.web[0]"),
        (finding("idle-nat-gateway", "nat-1"), 'module.vpc.aws_nat_gateway.this["eu-west-1a"]'),
        (finding("old-rds-snapshot", "arn:aws:rds:x", name="pre-upgrade"), "aws_db_snapshot.pre"),
    ],
)
def test_terraform_managed(index, f, address):
    annotate([f], index)

    assert (f.managed_by, f.iac_address, f.iac_source) == ("terraform", address, "prod.tfstate")
    assert address in f.recommendation and "terraform apply" in f.recommendation


def test_data_source_does_not_count_as_managed(index):
    f = finding("unattached-ebs-volume", "vol-looked-up")
    annotate([f], index)
    assert f.managed_by == "unmanaged"
    assert f.recommendation == "delete it"


def test_type_must_fit_the_check(index):
    # An instance ID matching a finding of an unrelated kind is not a match
    f = finding("unused-elastic-ip", "i-web")
    annotate([f], index)
    assert f.managed_by == "unmanaged"


def test_cloudformation_tag(index):
    f = finding("unattached-ebs-volume", "vol-x", tags={"aws:cloudformation:stack-name": "legacy"})
    annotate([f], index)
    assert (f.managed_by, f.iac_address) == ("cloudformation", "legacy")


def test_unsupported_state_version():
    with pytest.raises(ValueError, match="version"):
        StateIndex().add_state({"version": 3, "modules": []}, "old.tfstate")


def test_load_local_directory_skips_dot_terraform(tmp_path):
    (tmp_path / "envs" / "prod").mkdir(parents=True)
    (tmp_path / "envs" / "prod" / "terraform.tfstate").write_text(
        json.dumps(state(resource("aws_eip", "nat", instance({"id": "eipalloc-1"}))))
    )
    (tmp_path / ".terraform").mkdir()
    (tmp_path / ".terraform" / "terraform.tfstate").write_text("not json")  # backend config
    (tmp_path / "broken.tfstate").write_text("{oops")

    index, errors = load_states(boto3.Session(region_name=REGION), [str(tmp_path)])

    assert index.sources == [str(tmp_path / "envs" / "prod" / "terraform.tfstate")]
    assert len(errors) == 1 and "broken.tfstate" in errors[0]


def test_missing_path_is_an_error(tmp_path):
    _, errors = load_states(boto3.Session(region_name=REGION), [str(tmp_path / "nope")])
    assert len(errors) == 1 and "not found" in errors[0]


def test_load_s3_prefix_including_workspaces(session):
    s3 = session.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="tf-state")
    body = json.dumps(state(resource("aws_eip", "a", instance({"id": "eipalloc-1"}))))
    s3.put_object(Bucket="tf-state", Key="network/terraform.tfstate", Body=body)
    s3.put_object(Bucket="tf-state", Key="env:/staging/network/terraform.tfstate", Body=body)
    s3.put_object(Bucket="tf-state", Key="network/terraform.tfstate.backup", Body=body)

    index, errors = load_states(session, ["s3://tf-state/"])

    assert errors == []
    assert sorted(index.sources) == [
        "s3://tf-state/env:/staging/network/terraform.tfstate",
        "s3://tf-state/network/terraform.tfstate",
    ]


def test_load_single_s3_object_and_missing_bucket(session):
    s3 = session.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="tf-state")
    s3.put_object(Bucket="tf-state", Key="app.tfstate", Body=json.dumps(state()))

    index, errors = load_states(session, ["s3://tf-state/app.tfstate", "s3://no-such-bucket/"])

    assert index.sources == ["s3://tf-state/app.tfstate"]
    assert len(errors) == 1 and "NoSuchBucket" in errors[0]


def test_cli_marks_managed_and_unmanaged(tmp_path, capsys):
    with mock_aws():
        ec2 = boto3.client("ec2", region_name=REGION)
        managed = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10)["VolumeId"]
        unmanaged = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=20)["VolumeId"]
        state_file = tmp_path / "terraform.tfstate"
        state_file.write_text(
            json.dumps(state(resource("aws_ebs_volume", "data", instance({"id": managed}))))
        )

        assert main(["scan", "--region", REGION, "--tfstate", str(state_file), "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    by_id = {f["resource_id"]: f for f in report["findings"]}
    assert report["iac_checked"] is True
    assert by_id[managed]["managed_by"] == "terraform"
    assert by_id[managed]["iac_address"] == "aws_ebs_volume.data"
    assert by_id[unmanaged]["managed_by"] == "unmanaged"
