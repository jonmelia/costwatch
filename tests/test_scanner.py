import json

from costwatch import report
from costwatch.cli import main
from costwatch.scanner import scan
from tests.conftest import REGION


def test_scan_sorts_findings_by_cost_and_totals(session, ec2):
    ec2.allocate_address(Domain="vpc")  # ~$3.65
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=500, VolumeType="gp2")  # $50

    # Default config: moto's seeded snapshots are too new to count as old
    result = scan(session, regions=[REGION])

    assert [f.check for f in result.findings] == ["unattached-ebs-volume", "unused-elastic-ip"]
    assert round(result.total_monthly_cost, 2) == 53.65
    assert result.errors == []


def test_scan_records_errors_without_failing(session):
    def broken(session, region, config):
        session.client("ec2", region_name=region).describe_volumes(VolumeIds=["vol-missing"])

    result = scan(session, regions=[REGION], checks=[broken])

    assert result.findings == []
    assert len(result.errors) == 1 and "broken" in result.errors[0]


def test_json_report(session, ec2):
    ec2.allocate_address(Domain="vpc")

    data = json.loads(report.to_json(scan(session, regions=[REGION])))

    assert data["account_id"] == "123456789012"
    assert data["total_monthly_cost"] == 3.65
    assert data["findings"][0]["check"] == "unused-elastic-ip"


def test_cli_json_output(session, ec2, capsys):
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")

    assert main(["scan", "--region", REGION, "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["findings"][0]["check"] == "unattached-ebs-volume"


def test_cli_policy(capsys):
    assert main(["policy"]) == 0
    assert "ec2:DescribeVolumes" in capsys.readouterr().out


def test_errors_are_shortened_to_code_and_message():
    from botocore.exceptions import ClientError

    from costwatch.aws import short_error

    e = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "User x is\n not authorized " + "a" * 300}},
        "DescribeVolumes",
    )

    text = short_error(e)
    assert text.startswith("AccessDenied: User x is not authorized")
    assert len(text) == 160 and "\n" not in text


def test_unexpected_exception_in_a_check_is_contained(session, ec2):
    def buggy(session, region, config):
        raise KeyError("VolumeType")

    def fine(session, region, config):
        return []

    result = scan(session, regions=[REGION], checks=[buggy, fine])

    assert result.errors == [f"{REGION} buggy: KeyError: 'VolumeType'"]


def test_keyboard_interrupt_exits_cleanly(monkeypatch, capsys):
    import pytest

    from costwatch import cli

    def interrupted(argv=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "main", interrupted)
    with pytest.raises(SystemExit) as exit_info:
        cli.run()

    assert exit_info.value.code == 130
    assert "Interrupted" in capsys.readouterr().err
