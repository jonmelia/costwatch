import re
from datetime import UTC, datetime, timedelta

import boto3

from costwatch import pricing
from costwatch.aws import chunks, client, name_tag, tag_dict
from costwatch.metrics import daily_values
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
                tags=tag_dict(addr.get("Tags")),
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
                tags=tag_dict(instance.get("Tags")),
                description=(
                    f"{instance['InstanceType']} stopped for {(config.now - since).days} days; "
                    "its EBS volumes are still billed"
                ),
                monthly_cost=volume_cost.get(iid, 0.0),
                recommendation="Create an AMI or snapshots if needed, then terminate it.",
            )
        )
    return findings


def idle_instances(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    cutoff = config.now - timedelta(days=config.idle_days)
    running = [
        instance
        for page in ec2.get_paginator("describe_instances").paginate(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
        )
        for reservation in page["Reservations"]
        for instance in reservation["Instances"]
        if instance["LaunchTime"] <= cutoff
    ]
    if not running:
        return []

    cpu = daily_values(
        client(session, "cloudwatch", region),
        {
            i["InstanceId"]: ("AWS/EC2", "CPUUtilization", {"InstanceId": i["InstanceId"]})
            for i in running
        },
        "Maximum",
        config.idle_days,
        config.now,
    )

    findings = []
    for instance in running:
        values = cpu[instance["InstanceId"]]
        # Need most of the window covered, and CPU never above the threshold
        if len(values) < config.idle_days // 2 or max(values) >= config.idle_cpu_percent:
            continue
        cost = pricing.ec2_instance_monthly(instance["InstanceType"])
        if cost is None:
            price_note = " (price not in table)"
        elif instance.get("InstanceLifecycle") == "spot":
            price_note = " (spot: on-demand price shown, actual is lower)"
        else:
            price_note = ""
        findings.append(
            Finding(
                check="idle-ec2-instance",
                region=region,
                resource_id=instance["InstanceId"],
                name=name_tag(instance.get("Tags")),
                tags=tag_dict(instance.get("Tags")),
                description=(
                    f"{instance['InstanceType']} peak CPU {max(values):.1f}% over "
                    f"{config.idle_days} days{price_note}"
                ),
                monthly_cost=cost or 0.0,
                recommendation=(
                    "Stop or terminate it if unused; if it's a bastion, consider SSM Session "
                    "Manager; otherwise downsize."
                ),
            )
        )
    return findings


def _used_image_ids(session: boto3.Session, region: str) -> set[str]:
    ec2 = client(session, "ec2", region)
    used = set()
    for page in ec2.get_paginator("describe_instances").paginate():
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                if instance["State"]["Name"] != "terminated":
                    used.add(instance["ImageId"])
    for page in ec2.get_paginator("describe_launch_templates").paginate():
        for template in page["LaunchTemplates"]:
            versions = ec2.describe_launch_template_versions(
                LaunchTemplateId=template["LaunchTemplateId"], Versions=["$Default", "$Latest"]
            )["LaunchTemplateVersions"]
            for version in versions:
                if image_id := version.get("LaunchTemplateData", {}).get("ImageId"):
                    used.add(image_id)
    autoscaling = client(session, "autoscaling", region)
    for page in autoscaling.get_paginator("describe_launch_configurations").paginate():
        for config in page["LaunchConfigurations"]:
            used.add(config["ImageId"])
    return used


def unused_amis(session: boto3.Session, region: str, config: ScanConfig) -> list[Finding]:
    ec2 = client(session, "ec2", region)
    cutoff = config.now - timedelta(days=config.snapshot_age_days)
    old_images = [
        image
        for page in ec2.get_paginator("describe_images").paginate(Owners=["self"])
        for image in page["Images"]
        if datetime.fromisoformat(image["CreationDate"]) <= cutoff
    ]
    if not old_images:
        return []

    used = _used_image_ids(session, region)
    findings = []
    for image in old_images:
        if image["ImageId"] in used:
            continue
        size = sum(
            m["Ebs"].get("VolumeSize", 0)
            for m in image.get("BlockDeviceMappings", [])
            if "Ebs" in m
        )
        age_days = (config.now - datetime.fromisoformat(image["CreationDate"])).days
        findings.append(
            Finding(
                check="unused-ami",
                region=region,
                resource_id=image["ImageId"],
                name=image.get("Name"),
                tags=tag_dict(image.get("Tags")),
                description=(
                    f"AMI {age_days} days old with {size} GiB of snapshots, not used by any "
                    "instance or launch template (cost is an upper bound)"
                ),
                monthly_cost=pricing.snapshot_monthly(size),
                recommendation="Deregister the AMI, then delete its snapshots.",
            )
        )
    return findings
