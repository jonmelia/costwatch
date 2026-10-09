import re
from datetime import UTC, datetime, timedelta

import boto3

from costwatch import pricing
from costwatch.aws import chunks, client, name_tag
from costwatch.models import Finding, ScanConfig

# e.g. "User initiated (2024-01-15 10:30:00 GMT)"
_STOPPED_AT = re.compile(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (?:GMT|UTC)\)")


def unused_elastic_ips(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    findings = []
    for addr in ec2.describe_addresses()["Addresses"]:
        if addr.get("AssociationId") or addr.get("NetworkInterfaceId"):
            continue
        findings.append(
            Finding(
                check="unused-elastic-ip",
                region=region,
                resource_id=addr.get("AllocationId", addr["PublicIp"]),
                name=name_tag(addr.get("Tags")),
                description=f"Elastic IP {addr['PublicIp']} is not associated with anything",
                monthly_cost=pricing.public_ipv4_monthly(),
                recommendation="Release the address if you don't need to keep it.",
            )
        )
    return findings


def _stopped_since(instance: dict) -> datetime | None:
    match = _STOPPED_AT.search(instance.get("StateTransitionReason", ""))
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def long_stopped_instances(
    session: boto3.Session, region: str, config: ScanConfig
) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    cutoff = config.now - timedelta(days=config.stopped_days)

    stopped = []
    pages = ec2.get_paginator("describe_instances").paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]
    )
    for page in pages:
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                since = _stopped_since(instance)
                if since and since <= cutoff:
                    stopped.append((instance, since))
    if not stopped:
        return []

    # Stopped instances don't bill for compute, but their volumes still do
    volume_cost: dict[str, float] = {}
    for batch in chunks([i["InstanceId"] for i, _ in stopped], 200):
        pages = ec2.get_paginator("describe_volumes").paginate(
            Filters=[{"Name": "attachment.instance-id", "Values": batch}]
        )
        for page in pages:
            for vol in page["Volumes"]:
                for attachment in vol.get("Attachments", []):
                    cost = pricing.ebs_volume_monthly(
                        vol["VolumeType"], vol["Size"], vol.get("Iops"), vol.get("Throughput")
                    )
                    iid = attachment["InstanceId"]
                    volume_cost[iid] = volume_cost.get(iid, 0.0) + cost

    findings = []
    for instance, since in stopped:
        iid = instance["InstanceId"]
        findings.append(
            Finding(
                check="long-stopped-instance",
                region=region,
                resource_id=iid,
                name=name_tag(instance.get("Tags")),
                description=(
                    f"{instance['InstanceType']} stopped for {(config.now - since).days} days; "
                    "its EBS volumes are still billed"
                ),
                monthly_cost=volume_cost.get(iid, 0.0),
                recommendation="Create an AMI or snapshots if needed, then terminate it.",
            )
        )
    return findings
