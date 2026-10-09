from datetime import timedelta

import boto3

from costwatch import pricing
from costwatch.aws import client, name_tag, tag_dict
from costwatch.fmt import ago
from costwatch.models import Finding, ScanConfig

_LIFECYCLE_TAG_PREFIXES = ("aws:backup:", "aws:dlm:")


def unattached_volumes(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    findings = []
    pages = ec2.get_paginator("describe_volumes").paginate(
        Filters=[{"Name": "status", "Values": ["available"]}]
    )
    for page in pages:
        for vol in page["Volumes"]:
            age_days = (config.now - vol["CreateTime"]).days
            findings.append(
                Finding(
                    check="unattached-ebs-volume",
                    region=region,
                    resource_id=vol["VolumeId"],
                    name=name_tag(vol.get("Tags")),
                    tags=tag_dict(vol.get("Tags")),
                    description=(
                        f"{vol['Size']} GiB {vol['VolumeType']} volume not attached to any "
                        f"instance (created {ago(age_days)})"
                    ),
                    monthly_cost=pricing.ebs_volume_monthly(
                        region,
                        vol["VolumeType"],
                        vol["Size"],
                        vol.get("Iops"),
                        vol.get("Throughput"),
                    ),
                    recommendation="Snapshot it if the data matters, then delete the volume.",
                )
            )
    return findings


def old_snapshots(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    ec2 = client(session, "ec2", region)

    # Snapshots backing one of our AMIs are in use, even if old
    ami_snapshots = set()
    for page in ec2.get_paginator("describe_images").paginate(Owners=["self"]):
        for image in page["Images"]:
            for mapping in image.get("BlockDeviceMappings", []):
                if snapshot_id := mapping.get("Ebs", {}).get("SnapshotId"):
                    ami_snapshots.add(snapshot_id)

    cutoff = config.now - timedelta(days=config.snapshot_age_days)
    findings = []
    for page in ec2.get_paginator("describe_snapshots").paginate(OwnerIds=["self"]):
        for snap in page["Snapshots"]:
            if snap["StartTime"] > cutoff or snap["SnapshotId"] in ami_snapshots:
                continue
            tags = tag_dict(snap.get("Tags"))
            if any(k.startswith(_LIFECYCLE_TAG_PREFIXES) for k in tags):
                continue  # AWS Backup / Data Lifecycle Manager apply their own retention
            archived = snap.get("StorageTier") == "archive"
            age_days = (config.now - snap["StartTime"]).days
            findings.append(
                Finding(
                    check="old-ebs-snapshot",
                    region=region,
                    resource_id=snap["SnapshotId"],
                    name=tags.get("Name"),
                    tags=tags,
                    description=(
                        f"{snap['VolumeSize']} GiB {'archived ' if archived else ''}snapshot, "
                        f"{age_days} days old, not used by any AMI "
                        "(cost is an upper bound; snapshots are incremental)"
                    ),
                    monthly_cost=pricing.snapshot_monthly(region, snap["VolumeSize"], archived),
                    recommendation=(
                        "Delete if no longer needed."
                        if archived
                        else "Delete if no longer needed, or move to the archive tier "
                        "for long-term retention."
                    ),
                )
            )
    return findings


def gp2_volumes(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    findings = []
    # Unattached gp2 volumes are already reported as waste in full
    pages = ec2.get_paginator("describe_volumes").paginate(
        Filters=[
            {"Name": "volume-type", "Values": ["gp2"]},
            {"Name": "status", "Values": ["in-use"]},
        ]
    )
    for page in pages:
        for vol in page["Volumes"]:
            savings = pricing.gp2_to_gp3_monthly_savings(region, vol["Size"])
            if savings <= 0:
                continue
            findings.append(
                Finding(
                    check="gp2-volume",
                    region=region,
                    resource_id=vol["VolumeId"],
                    name=name_tag(vol.get("Tags")),
                    tags=tag_dict(vol.get("Tags")),
                    description=(
                        f"{vol['Size']} GiB gp2 volume; gp3 with the same performance "
                        "is cheaper (cost shown is the saving)"
                    ),
                    monthly_cost=savings,
                    recommendation="Modify the volume type to gp3; no downtime needed.",
                )
            )
    return findings
