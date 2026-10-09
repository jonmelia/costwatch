import csv
import io
import json

import boto3
import pytest
from moto import mock_aws

from costwatch.checks import ALL_CHECKS, CHECK_IDS, select_checks
from costwatch.cli import EXIT_ERROR, EXIT_OK, EXIT_OVER_THRESHOLD, main
from tests.conftest import REGION


@pytest.fixture
def account():
    """A fake account with a $50 volume, an ignored $10 volume and a $3.65 Elastic IP."""
    with mock_aws():
        ec2 = boto3.client("ec2", region_name=REGION)
        big = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=500, VolumeType="gp2")
        small = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=100, VolumeType="gp2")
        ec2.create_tags(
            Resources=[small["VolumeId"]], Tags=[{"Key": "costwatch:ignore", "Value": "true"}]
        )
        eip = ec2.allocate_address(Domain="vpc")
        yield {"big": big["VolumeId"], "small": small["VolumeId"], "eip": eip["AllocationId"]}


def scan_json(capsys, *args):
    code = main(["scan", "--region", REGION, "--format", "json", *args])
    return code, json.loads(capsys.readouterr().out)


def test_select_checks():
    assert select_checks() == ALL_CHECKS
    only = select_checks(["idle-load-balancer"])
    assert [f.__name__ for f in only] == ["idle_load_balancers", "idle_classic_load_balancers"]
    assert len(select_checks(exclude=["idle-load-balancer"])) == len(ALL_CHECKS) - 2
    with pytest.raises(ValueError, match="nope"):
        select_checks(["unattached-ebs-volume", "nope"])


def test_checks_command_lists_every_check(capsys):
    assert main(["checks"]) == EXIT_OK
    out = capsys.readouterr().out
    assert all(check_id in out for check_id in CHECK_IDS)


def test_unknown_check_is_an_error(capsys):
    assert main(["scan", "--checks", "nope"]) == EXIT_ERROR
    assert "unknown check" in capsys.readouterr().err


def test_ignore_tag_ids_and_file(account, capsys, tmp_path):
    code, report = scan_json(capsys, "--checks", "unattached-ebs-volume,unused-elastic-ip")
    assert code == EXIT_OK
    assert {f["resource_id"] for f in report["findings"]} == {account["big"], account["eip"]}
    assert report["ignored"] == 1  # the tagged volume

    ignore_file = tmp_path / "ignore.txt"
    ignore_file.write_text(f"# known\n{account['eip']}  # kept for DNS\n")
    _, report = scan_json(capsys, "--ignore", account["big"], "--ignore-file", str(ignore_file))
    assert account["big"] not in {f["resource_id"] for f in report["findings"]}
    assert account["eip"] not in {f["resource_id"] for f in report["findings"]}


def test_only_selected_checks_run(account, capsys):
    _, report = scan_json(capsys, "--checks", "unused-elastic-ip")
    assert [f["check"] for f in report["findings"]] == ["unused-elastic-ip"]


def test_json_metadata(account, capsys):
    _, report = scan_json(capsys)
    assert report["schema_version"] == 1
    assert report["costwatch_version"]
    assert report["generated_at"].endswith("+00:00")
    assert report["prices_as_of"] == "2026-01-01T00:00:00Z"  # fixture prices


def test_csv_output(account, capsys):
    assert main(["scan", "--region", REGION, "--format", "csv"]) == EXIT_OK
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert rows[0]["resource_id"] == account["big"]
    assert rows[0]["monthly_cost"] == "50.00"


def test_markdown_output_to_file(account, tmp_path, capsys):
    out = tmp_path / "report.md"
    assert main(["scan", "--region", REGION, "--format", "markdown", "-o", str(out)]) == EXIT_OK
    text = out.read_text()
    assert text.startswith("## AWS waste: ~$53.65/month")
    assert f"`{account['big']}`" in text
    assert "1 finding(s) ignored." in text


def test_table_output_to_file(account, tmp_path, capsys):
    out = tmp_path / "report.txt"
    assert main(["scan", "--region", REGION, "-o", str(out)]) == EXIT_OK
    assert "Total: ~$53.65/month" in out.read_text()


@pytest.mark.parametrize(("limit", "code"), [("50", EXIT_OVER_THRESHOLD), ("100", EXIT_OK)])
def test_fail_over(account, capsys, limit, code):
    assert main(["scan", "--region", REGION, "--format", "json", "--fail-over", limit]) == code


def test_role_arn_is_assumed(account, capsys):
    code, report = scan_json(
        capsys,
        "--role-arn",
        "arn:aws:iam::111122223333:role/costwatch-read",
        "--external-id",
        "ext-123",
    )
    assert code == EXIT_OK
    assert report["account_id"] == "111122223333"
