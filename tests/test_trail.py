from datetime import UTC, datetime

import pytest

from costwatch.checks import trail as trail_check
from costwatch.checks.trail import duplicate_trails, records_management_events
from costwatch.models import ScanConfig
from tests.conftest import REGION

ACCOUNT = "123456789012"
CONFIG = ScanConfig(now=datetime(2026, 11, 7, tzinfo=UTC))


def trail(name, home=REGION, org=False):
    return {
        "Name": name,
        "TrailARN": f"arn:aws:cloudtrail:{home}:{ACCOUNT}:trail/{name}",
        "HomeRegion": home,
        "IsOrganizationTrail": org,
    }


def group(usage, amount):
    return {"Keys": [usage], "Metrics": {"UnblendedCost": {"Amount": str(amount)}}}


class Fake:
    """moto has neither organization trails nor Cost Explorer's usage types."""

    def __init__(self, trails, pages=(), selectors=None):
        self.trails, self.pages, self.asked = trails, list(pages), []
        self.selectors = selectors or {}

    def get_event_selectors(self, TrailName):
        return self.selectors.get(TrailName.rsplit("/", 1)[1], {})

    def describe_trails(self, includeShadowTrails):
        return {"trailList": self.trails}

    def get_caller_identity(self):
        return {"Account": ACCOUNT}

    def get_cost_and_usage(self, **kwargs):
        self.asked.append(kwargs)
        return self.pages.pop(0)


@pytest.fixture
def fake(monkeypatch):
    def use(trails, pages=(), selectors=None):
        found = Fake(trails, pages, selectors)
        monkeypatch.setattr(trail_check, "client", lambda session, name, region: found)
        return found

    return use


def test_a_second_trail_is_priced_from_the_bill_and_the_organization_trail_is_left_out(fake):
    bill = fake(
        [trail("organization-trail", org=True), trail("mine")],
        [
            {
                "ResultsByTime": [{"Groups": [group("EUW2-PaidEventsRecorded", 20)]}],
                "NextPageToken": "more",
            },
            {
                "ResultsByTime": [
                    {
                        "Groups": [
                            group("USE1-PaidEventsRecorded", 4.5),
                            group("EUW2-DataEventsRecorded", 9),
                        ]
                    }
                ]
            },
        ],
    )
    (found,) = duplicate_trails(None, REGION, CONFIG)
    assert found.check == "duplicate-trail" and found.name == "mine"
    assert found.monthly_cost == 24.5
    assert "$24.50" in found.description
    assert "One of 2 trails recording management events" in found.description
    filtered = bill.asked[0]["Filter"]["Dimensions"]
    assert filtered == {"Key": "LINKED_ACCOUNT", "Values": [ACCOUNT]}
    assert bill.asked[1]["NextPageToken"] == "more"


def test_two_extra_trails_share_the_cost(fake):
    fake(
        [trail("a"), trail("b")],
        [{"ResultsByTime": [{"Groups": [group("EUW2-PaidEventsRecorded", 10)]}]}],
    )
    assert [f.monthly_cost for f in duplicate_trails(None, REGION, CONFIG)] == [5, 5]


@pytest.mark.parametrize(
    "trails",
    [
        [],
        [trail("only")],
        [trail("organization-trail", org=True)],
        [trail("org-a", org=True), trail("org-b", org=True)],
        [trail("elsewhere", home="ap-south-1"), trail("org", org=True)],
    ],
)
def test_nothing_is_asked_of_the_bill_without_a_second_trail_here(fake, trails):
    bill = fake(trails)
    assert duplicate_trails(None, REGION, CONFIG) == []
    assert bill.asked == []


def test_a_small_charge_isnt_reported(fake):
    fake(
        [trail("a"), trail("b")],
        [{"ResultsByTime": [{"Groups": [group("EUW2-PaidEventsRecorded", 0.5)]}]}],
    )
    assert duplicate_trails(None, REGION, CONFIG) == []


DATA_ONLY_BASIC = {
    "EventSelectors": [
        {"IncludeManagementEvents": False, "DataResources": [{"Type": "AWS::S3::Object"}]}
    ]
}
DATA_ONLY_ADVANCED = {
    "AdvancedEventSelectors": [
        {
            "FieldSelectors": [
                {"Field": "eventCategory", "Equals": ["Data"]},
                {"Field": "resources.type", "Equals": ["AWS::S3::Object"]},
            ]
        }
    ]
}


@pytest.mark.parametrize("selectors", [DATA_ONLY_BASIC, DATA_ONLY_ADVANCED])
def test_a_trail_kept_for_data_events_only_isnt_a_copy(fake, selectors):
    bill = fake(
        [trail("organization-trail", org=True), trail("s3-data")],
        [{"ResultsByTime": [{"Groups": [group("EUW2-PaidEventsRecorded", 24)]}]}],
        {"s3-data": selectors},
    )
    assert duplicate_trails(None, REGION, CONFIG) == []
    assert bill.asked == []


def test_only_the_trails_recording_management_events_are_reported(fake):
    advanced = {
        "AdvancedEventSelectors": [
            {"FieldSelectors": [{"Field": "eventCategory", "Equals": ["Management"]}]}
        ]
    }
    fake(
        [trail("organization-trail", org=True), trail("s3-data"), trail("audit")],
        [{"ResultsByTime": [{"Groups": [group("EUW2-PaidEventsRecorded", 24)]}]}],
        {"s3-data": DATA_ONLY_BASIC, "audit": advanced},
    )
    (found,) = duplicate_trails(None, REGION, CONFIG)
    assert found.name == "audit" and found.monthly_cost == 24


@pytest.mark.parametrize(
    "selectors",
    [
        {},
        {"EventSelectors": [{"ReadWriteType": "All"}]},
        {"EventSelectors": [DATA_ONLY_BASIC["EventSelectors"][0], {}]},
    ],
)
def test_management_events_are_recorded_unless_turned_off(selectors):
    assert records_management_events(selectors)
