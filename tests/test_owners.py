import json
from datetime import UTC, datetime

import boto3
import pytest
from botocore.stub import Stubber

from costwatch import owners
from costwatch.models import Finding
from costwatch.owners import identity, owner_from_tags, resolve_owners


def finding(check="unattached-ebs-volume", resource_id="vol-1", region="eu-west-1", **kw):
    return Finding(
        check=check,
        region=region,
        resource_id=resource_id,
        description="",
        monthly_cost=10.0,
        recommendation="",
        **kw,
    )


def event(name, user_identity, username=None):
    e = {
        "EventId": name,
        "EventName": name,
        "EventTime": datetime(2026, 9, 1, tzinfo=UTC),
        "CloudTrailEvent": json.dumps({"userIdentity": user_identity}),
    }
    if username:
        e["Username"] = username
    return e


SSO_USER = {
    "type": "AssumedRole",
    "arn": "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_Admin_abc/alice@example.com",
}


@pytest.fixture
def stubbed(monkeypatch):
    """Route owner lookups through stubbed CloudTrail clients, one per region."""
    session = boto3.Session(
        aws_access_key_id="x", aws_secret_access_key="x", region_name="eu-west-1"
    )
    stubs: dict[str, Stubber] = {}

    def make(region):
        trail = session.client("cloudtrail", region_name=region)
        stubs[region] = Stubber(trail)
        return trail, stubs[region]

    clients = {}

    def fake_client(_session, service, region, config=None):
        assert service == "cloudtrail"
        return clients[region]

    monkeypatch.setattr(owners, "client", fake_client)

    def add(region, events_by_value: dict[str, list[dict]]):
        trail, stub = make(region)
        clients[region] = trail
        for value, events in events_by_value.items():
            stub.add_response(
                "lookup_events",
                {"Events": events},
                {
                    "LookupAttributes": [{"AttributeKey": "ResourceName", "AttributeValue": value}],
                    "MaxResults": 50,
                },
            )
        stub.activate()
        return stub

    return session, add


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"Owner": "alice"}, ("alice", "Owner")),
        ({"created-by": "bob", "Name": "x"}, ("bob", "created-by")),
        ({"team": "payments"}, ("payments", "team")),
        ({"Owner": ""}, None),
        ({"Name": "x"}, None),
    ],
)
def test_owner_from_tags(tags, expected):
    assert owner_from_tags(tags) == expected


@pytest.mark.parametrize(
    ("user_identity", "username", "expected"),
    [
        (SSO_USER, "alice@example.com", "alice@example.com (AWSReservedSSO_Admin_abc)"),
        ({"type": "IAMUser", "userName": "deploy-bot"}, "deploy-bot", "deploy-bot"),
        ({"type": "Root"}, "root", "root"),
        ({"type": "AWSService", "invokedBy": "backup.amazonaws.com"}, None, "backup.amazonaws.com"),
    ],
)
def test_identity(user_identity, username, expected):
    assert identity(event("CreateVolume", user_identity, username)) == expected


def test_tag_owner_wins_without_cloudtrail_call(stubbed):
    session, add = stubbed
    stub = add("eu-west-1", {})  # any CloudTrail call would fail the stub
    f = finding(tags={"Owner": "alice"})

    assert resolve_owners(session, [f]) == []

    assert (f.owner, f.owner_source) == ("alice", "tag:Owner")
    stub.assert_no_pending_responses()


def test_creator_found_in_cloudtrail(stubbed):
    session, add = stubbed
    stub = add(
        "eu-west-1",
        {
            "vol-1": [
                event("AttachVolume", {"type": "IAMUser", "userName": "someone-else"}),
                event("CreateVolume", SSO_USER),
            ]
        },
    )
    f = finding()

    assert resolve_owners(session, [f]) == []

    assert f.owner == "alice@example.com (AWSReservedSSO_Admin_abc)"
    assert f.owner_source == "cloudtrail"
    stub.assert_no_pending_responses()


def test_falls_back_to_name_lookup(stubbed):
    session, add = stubbed
    arn = "arn:aws:rds:eu-west-1:123456789012:snapshot:pre-upgrade"
    add(
        "eu-west-1",
        {
            arn: [],
            "pre-upgrade": [event("CreateDBSnapshot", {"type": "IAMUser", "userName": "dba"})],
        },
    )
    f = finding(check="old-rds-snapshot", resource_id=arn, name="pre-upgrade")

    resolve_owners(session, [f])

    assert f.owner == "dba"


def test_unknown_when_no_create_event(stubbed):
    session, add = stubbed
    add("eu-west-1", {"vol-1": []})
    f = finding()

    assert resolve_owners(session, [f]) == []
    assert f.owner is None


def test_access_denied_is_reported_not_raised(stubbed):
    session, add = stubbed
    trail_stub = add("eu-west-1", {})
    trail_stub.add_client_error("lookup_events", "AccessDeniedException", "not allowed")
    f = finding()

    errors = resolve_owners(session, [f])

    assert len(errors) == 1 and "AccessDenied" in errors[0]
    assert f.owner is None
