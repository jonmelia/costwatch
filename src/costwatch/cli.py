import argparse
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from rich.console import Console

from costwatch import __version__, report
from costwatch.iam import policy_json
from costwatch.models import ScanConfig
from costwatch.owners import resolve_owners
from costwatch.scanner import scan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="costwatch", description="Find wasted AWS spend.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="Scan an AWS account for idle and orphaned resources")
    scan_p.add_argument("--profile", help="AWS profile (defaults to $AWS_PROFILE)")
    scan_p.add_argument(
        "--region",
        action="append",
        dest="regions",
        help="Region to scan; repeat for several (default: all enabled regions)",
    )
    scan_p.add_argument(
        "--snapshot-age-days",
        type=int,
        default=90,
        help="Flag EBS and manual RDS snapshots older than this (default: 90)",
    )
    scan_p.add_argument("--stopped-days", type=int, default=30)
    scan_p.add_argument(
        "--idle-days",
        type=int,
        default=14,
        help="Days of CloudWatch metrics the idle checks look at (default: 14)",
    )
    scan_p.add_argument(
        "--min-cost", type=float, default=0.0, help="Hide findings under this $/month"
    )
    scan_p.add_argument(
        "--owners",
        action="store_true",
        help=(
            "Find who owns each resource: owner tags, then who created it in CloudTrail "
            "(last 90 days only; slower)"
        ),
    )
    scan_p.add_argument("--json", action="store_true", help="Output JSON instead of a table")

    sub.add_parser("policy", help="Print the IAM policy costwatch needs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "policy":
        print(policy_json())
        return 0

    console = Console(stderr=True)
    config = ScanConfig(
        snapshot_age_days=args.snapshot_age_days,
        stopped_days=args.stopped_days,
        idle_days=args.idle_days,
    )
    try:
        session = boto3.Session(profile_name=args.profile)
        with console.status("Scanning AWS account..."):
            result = scan(session, regions=args.regions, config=config)
        result.findings = [f for f in result.findings if f.monthly_cost >= args.min_cost]
        if args.owners:
            with console.status("Looking up owners in CloudTrail..."):
                result.errors.extend(resolve_owners(session, result.findings))
            result.owners_checked = True
    except NoCredentialsError:
        console.print("[red]No AWS credentials found.[/red] Set AWS_PROFILE or pass --profile.")
        return 2
    except (ClientError, BotoCoreError) as e:
        console.print(f"[red]AWS error:[/red] {e}")
        return 2

    if args.json:
        print(report.to_json(result))
    else:
        report.print_table(result, Console())
    return 0


if __name__ == "__main__":
    sys.exit(main())
