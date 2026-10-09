"""Mark findings as managed by Terraform or CloudFormation, or unmanaged.

Idle and unmanaged usually means a click-ops leftover, the safest kind of finding to clean up.
Idle and Terraform-managed should be removed from the code, not deleted behind Terraform's back.

Terraform state can contain secrets. Only identifier attributes are read, in memory; nothing
else from the state is kept or printed.
"""

import json
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from costwatch.aws import client, short_error
from costwatch.models import Finding

# Attribute keys whose values identify an AWS resource, at any nesting depth
# (e.g. aws_instance.root_block_device[0].volume_id)
_ID_KEYS = {
    "id",
    "arn",
    "identifier",
    "allocation_id",
    "volume_id",
    "db_snapshot_arn",
    "db_snapshot_identifier",
    "db_cluster_snapshot_arn",
    "db_cluster_snapshot_identifier",
}

# Which Terraform resource types can own each kind of finding
TERRAFORM_TYPES = {
    "unattached-ebs-volume": {"aws_ebs_volume"},
    "gp2-volume": {"aws_ebs_volume", "aws_instance"},
    "old-ebs-snapshot": {"aws_ebs_snapshot", "aws_ebs_snapshot_copy"},
    "unused-elastic-ip": {"aws_eip"},
    "long-stopped-instance": {"aws_instance"},
    "idle-ec2-instance": {"aws_instance"},
    "unused-ami": {"aws_ami", "aws_ami_copy", "aws_ami_from_instance"},
    "idle-load-balancer": {"aws_lb", "aws_alb", "aws_elb"},
    "idle-nat-gateway": {"aws_nat_gateway"},
    "old-rds-snapshot": {"aws_db_snapshot", "aws_db_snapshot_copy", "aws_db_cluster_snapshot"},
    "idle-rds-instance": {"aws_db_instance", "aws_rds_cluster_instance"},
    "log-group-no-retention": {"aws_cloudwatch_log_group"},
}

CLOUDFORMATION_STACK_TAG = "aws:cloudformation:stack-name"


@dataclass(frozen=True)
class StateResource:
    type: str
    address: str
    source: str


@dataclass
class StateIndex:
    by_value: dict[str, list[StateResource]] = field(default_factory=lambda: defaultdict(list))
    sources: list[str] = field(default_factory=list)

    def add_state(self, state: dict, source: str) -> None:
        if state.get("version") != 4:
            raise ValueError(f"unsupported state version {state.get('version')!r} (need 4)")
        self.sources.append(source)
        for resource in state.get("resources", []):
            if resource.get("mode") != "managed":
                continue  # data sources read existing infrastructure; they don't own it
            base = f"{resource['type']}.{resource['name']}"
            if module := resource.get("module"):
                base = f"{module}.{base}"
            for instance in resource.get("instances", []):
                address = base
                if "index_key" in instance:
                    address += f"[{json.dumps(instance['index_key'])}]"
                entry = StateResource(resource["type"], address, source)
                for value in _identifiers(instance.get("attributes") or {}):
                    self.by_value[value].append(entry)

    def lookup(self, finding: Finding) -> StateResource | None:
        allowed = TERRAFORM_TYPES.get(finding.check, set())
        for value in (finding.resource_id, finding.name):
            for entry in self.by_value.get(value or "", []):
                if entry.type in allowed:
                    return entry
        return None


def _identifiers(value, key: str | None = None) -> Iterator[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _identifiers(v, k)
    elif isinstance(value, list):
        for item in value:
            yield from _identifiers(item, key)
    elif isinstance(value, str) and value and key in _ID_KEYS:
        yield value


def _local_states(location: str) -> Iterator[tuple[str, str]]:
    path = Path(location).expanduser()
    if path.is_dir():
        files = sorted(
            p for p in path.rglob("*.tfstate") if ".terraform" not in p.relative_to(path).parts
        )
        if not files:
            raise FileNotFoundError(f"no .tfstate files under {path}")
    elif path.is_file():
        files = [path]
    else:
        raise FileNotFoundError(f"{path} not found")
    for file in files:
        yield str(file), file.read_text()


def _s3_states(session: boto3.Session, location: str) -> Iterator[tuple[str, str]]:
    bucket, _, key = location.removeprefix("s3://").partition("/")
    s3 = client(session, "s3", session.region_name or "us-east-1")
    if key and not key.endswith("/"):
        keys = [key]
    else:
        # A prefix: every state file under it, including workspaces (env:/<name>/...)
        keys = [
            obj["Key"]
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key)
            for obj in page.get("Contents", [])
            if obj["Key"].endswith(".tfstate")
        ]
        if not keys:
            raise FileNotFoundError(f"no .tfstate objects under {location}")
    for k in keys:
        body = s3.get_object(Bucket=bucket, Key=k)["Body"].read()
        yield f"s3://{bucket}/{k}", body.decode()


def load_states(session: boto3.Session, locations: list[str]) -> tuple[StateIndex, list[str]]:
    """Load every state file from local paths and s3:// URLs. Returns (index, errors)."""
    index = StateIndex()
    errors = []
    for location in locations:
        try:
            states = (
                _s3_states(session, location)
                if location.startswith("s3://")
                else _local_states(location)
            )
            for source, text in states:
                try:
                    index.add_state(json.loads(text), source)
                except (ValueError, KeyError) as e:
                    errors.append(f"terraform state {source}: {e}")
        except (ClientError, BotoCoreError) as e:
            errors.append(f"terraform state {location}: {short_error(e)}")
        except OSError as e:
            errors.append(f"terraform state {location}: {e}")
    return index, errors


def annotate(findings: list[Finding], index: StateIndex) -> None:
    """Set managed_by / iac_address / iac_source on each finding, in place."""
    for finding in findings:
        if stack := finding.tags.get(CLOUDFORMATION_STACK_TAG):
            finding.managed_by, finding.iac_address = "cloudformation", stack
            finding.recommendation = (
                f"Managed by CloudFormation stack {stack}: remove it from the template and "
                "update the stack rather than deleting it directly."
            )
        elif entry := index.lookup(finding):
            finding.managed_by = "terraform"
            finding.iac_address, finding.iac_source = entry.address, entry.source
            finding.recommendation = (
                f"Managed by Terraform ({entry.address}): remove it from the code and run "
                "terraform apply rather than deleting it directly, which would cause drift."
            )
        else:
            finding.managed_by = "unmanaged"
