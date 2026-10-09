import pytest

from costwatch.filters import is_ignored
from costwatch.models import Finding


def finding(**kw):
    defaults = dict(
        check="x",
        region="r",
        resource_id="vol-1",
        description="",
        monthly_cost=1.0,
        recommendation="",
        name="data",
    )
    return Finding(**{**defaults, **kw})


@pytest.mark.parametrize(
    ("tags", "ids", "ignored"),
    [
        ({}, set(), False),
        ({}, {"vol-1"}, True),
        ({}, {"data"}, True),
        ({"costwatch:ignore": "true"}, set(), True),
        ({"costwatch:ignore": ""}, set(), True),
        ({"costwatch:ignore": "keep for audit"}, set(), True),
        ({"costwatch:ignore": "False"}, set(), False),
        ({"costwatch:ignore": "no"}, set(), False),
    ],
)
def test_is_ignored(tags, ids, ignored):
    assert is_ignored(finding(tags=tags), ids) is ignored
