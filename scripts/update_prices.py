"""Build src/costwatch/data/prices.json.gz from AWS's public price list.

Streams each region's EC2, RDS and CloudWatch on-demand price files (no AWS account needed) and
keeps only the prices costwatch uses.

    uv run python scripts/update_prices.py                       # all regions
    uv run python scripts/update_prices.py --region eu-west-2    # just some (for testing)
"""

import argparse
import csv
import gzip
import io
import json
import re
import sys
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

BASE = "https://pricing.us-east-1.amazonaws.com"
OUTPUT = Path(__file__).parent.parent / "src" / "costwatch" / "data" / "prices.json.gz"
REAL_REGION = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d$")
# Usage types carry a region prefix outside us-east-1, e.g. EUW2-EBS:VolumeUsage.gp3
REGION_PREFIX = re.compile(r"^[A-Z]{2,4}\d-")

RDS_ENGINES = {"PostgreSQL", "MySQL", "MariaDB", "Aurora PostgreSQL", "Aurora MySQL"}
RDS_STORAGE = {
    "RDS:GP2-Storage": "gp2",
    "RDS:GP3-Storage": "gp3",
    "RDS:PIOPS-Storage": "io1",
    "RDS:PIOPS-Storage-IO2": "io2",
    "RDS:StorageUsage": "standard",
}
LB_FAMILIES = {
    "Load Balancer-Application": "application",
    "Load Balancer-Network": "network",
    "Load Balancer-Gateway": "gateway",
    "Load Balancer": "classic",
}


def fetch_json(path: str) -> dict:
    with urllib.request.urlopen(BASE + path) as response:
        return json.load(response)


def rows(path: str):
    """Yield each price row of a price-list CSV as a dict, streaming."""
    with urllib.request.urlopen(BASE + path.removesuffix(".json") + ".csv") as response:
        reader = csv.reader(io.TextIOWrapper(response, encoding="utf-8"))
        for _ in range(5):  # format version, disclaimer, publication date, version, offer code
            next(reader)
        header = next(reader)
        for row in reader:
            record = dict(zip(header, row, strict=False))
            if (
                record.get("TermType") == "OnDemand"
                and record.get("Location Type", "AWS Region") == "AWS Region"
            ):
                record["usage"] = REGION_PREFIX.sub("", record.get("usageType", ""))
                yield record


def price(record: dict) -> float:
    return float(record["PricePerUnit"])


def region_prices(region: str, urls: dict[str, str]) -> dict:
    p: dict = {
        "ebs_gb_month": {},
        "ebs_iops_month": {},
        "lb_hour": {},
        "ec2_hour": {},
        "rds_hour": {},
        "rds_storage_gb_month": {},
    }

    for r in rows(urls["AmazonEC2"]):
        family, usage = r["Product Family"], r["usage"]
        volume = r.get("Volume API Name", "")
        if family == "Storage" and usage.startswith("EBS:VolumeUsage") and volume:
            p["ebs_gb_month"][volume] = price(r)
        elif family == "System Operation" and r.get("Group") == "EBS IOPS" and volume:
            p["ebs_iops_month"][volume] = price(r)
        elif family == "Provisioned Throughput" and volume == "gp3":
            # Published per GiB/s-month; AWS bills gp3 throughput per MiB/s-month
            p["gp3_throughput_mibps_month"] = price(r) / 1024
        elif family == "Storage Snapshot" and usage == "EBS:SnapshotUsage":
            p["snapshot_gb_month"] = price(r)
        elif family == "Storage Snapshot" and usage == "EBS:SnapshotArchiveStorage":
            p["snapshot_archive_gb_month"] = price(r)
        elif family == "NAT Gateway" and usage == "NatGateway-Hours":
            p["nat_gateway_hour"] = price(r)
        elif family in LB_FAMILIES and usage == "LoadBalancerUsage":
            p["lb_hour"][LB_FAMILIES[family]] = price(r)
        elif (
            family.startswith("Compute Instance")
            and usage.startswith("BoxUsage:")
            and r.get("Operating System") == "Linux"
            and r.get("Tenancy") == "Shared"
            and r.get("Pre Installed S/W") == "NA"
            and r.get("CapacityStatus") == "Used"
            and r.get("License Model") == "No License required"
            and r.get("MarketOption", "OnDemand") == "OnDemand"
        ):
            p["ec2_hour"][r["Instance Type"]] = price(r)

    for r in rows(urls["AmazonRDS"]):
        family, usage = r["Product Family"], r["usage"]
        if family == "Database Instance" and r.get("Database Engine") in RDS_ENGINES:
            if r.get("License Model", "").lower() != "no license required":
                continue
            prefix, _, instance_class = usage.partition(":")
            slot = {"InstanceUsage": 0, "Multi-AZUsage": 1}.get(prefix)
            if slot is None or not instance_class:
                continue
            engine_prices = p["rds_hour"].setdefault(r["Database Engine"], {})
            engine_prices.setdefault(instance_class, [None, None])[slot] = price(r)
        elif family == "Database Storage" and usage in RDS_STORAGE:
            p["rds_storage_gb_month"][RDS_STORAGE[usage]] = price(r)
        elif family == "Storage Snapshot" and usage == "RDS:ChargedBackupUsage":
            p["rds_backup_gb_month"] = price(r)
        elif family == "Storage Snapshot" and usage == "Aurora:BackupUsage":
            p["aurora_backup_gb_month"] = price(r)

    if "AmazonCloudWatch" in urls:
        for r in rows(urls["AmazonCloudWatch"]):
            if r["Product Family"] == "Storage Snapshot" and r["usage"] == "TimedStorage-ByteHrs":
                p["logs_gb_month"] = price(r)

    return p


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--region", action="append", help="Only these regions")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    services = ("AmazonEC2", "AmazonRDS", "AmazonCloudWatch")
    indexes = {s: fetch_json(f"/offers/v1.0/aws/{s}/current/region_index.json") for s in services}
    regions = sorted(r for r in indexes["AmazonEC2"]["regions"] if REAL_REGION.match(r))
    if args.region:
        regions = [r for r in regions if r in args.region]

    result: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for region in regions:
            urls = {
                s: indexes[s]["regions"][region]["currentVersionUrl"]
                for s in services
                if region in indexes[s]["regions"]
            }
            if "AmazonRDS" not in urls:
                print(f"skip {region}: no RDS price list", file=sys.stderr)
                continue
            futures[pool.submit(region_prices, region, urls)] = region
        for future in as_completed(futures):
            region = futures[future]
            result[region] = future.result()
            print(f"{region}: {len(result[region]['ec2_hour'])} instance types", file=sys.stderr)

    missing = {
        r: k
        for r, p in result.items()
        for k in ("snapshot_gb_month", "nat_gateway_hour")
        if k not in p
    }
    if missing:
        print(f"warning: missing prices {missing}", file=sys.stderr)

    data = {
        "source": BASE + "/offers/v1.0/aws/index.json",
        "publication_date": indexes["AmazonEC2"].get("publicationDate"),
        "regions": dict(sorted(result.items())),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 so identical prices give an identical file, and no spurious monthly diff
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    with gzip.GzipFile(args.output, "wb", mtime=0) as f:
        f.write(payload)
    print(f"wrote {args.output} ({args.output.stat().st_size // 1024} KiB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
