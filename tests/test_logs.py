import boto3
import pytest
from botocore.stub import Stubber

from costwatch.checks import logs as logs_check
from costwatch.models import ScanConfig

GIB = 2**30


@pytest.fixture
def stubbed_logs(monkeypatch):
    session = boto3.Session(
        aws_access_key_id="x", aws_secret_access_key="x", region_name="us-east-1"
    )
    client = session.client("logs")
    stub = Stubber(client)
    monkeypatch.setattr(logs_check, "client", lambda *_args, **_kw: client)
    return session, stub


def test_large_log_group_without_retention_is_flagged(stubbed_logs):
    session, stub = stubbed_logs
    stub.add_response(
        "describe_log_groups",
        {
            "logGroups": [
                {"logGroupName": "/app/forever", "storedBytes": 50 * GIB},
                {"logGroupName": "/app/kept-30d", "storedBytes": 50 * GIB, "retentionInDays": 30},
                {"logGroupName": "/app/tiny", "storedBytes": 10 * 2**20},
            ]
        },
        {},
    )
    stub.activate()

    findings = logs_check.log_groups_without_retention(session, "us-east-1", ScanConfig())

    assert [f.resource_id for f in findings] == ["/app/forever"]
    assert findings[0].monthly_cost == pytest.approx(1.5)  # 50 GiB * $0.03
    stub.assert_no_pending_responses()
