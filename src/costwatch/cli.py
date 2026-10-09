import argparse
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
from rich.console import Console

from costwatch import __version__, report
from costwatch.aws import short_error
from costwatch.checks import CHECK_IDS, CHECKS, select_checks
from costwatch.filters import IGNORE_TAG, apply_ignores, read_ignore_file
from costwatch.iac import annotate, load_states
from costwatch.iam import policy_json
from costwatch.models import ScanConfig
from costwatch.owners import resolve_owners
from costwatch.scanner import ScanResult, scan

EXIT_OK, EXIT_OVER_THRESHOLD, EXIT_ERROR = 0, 1, 2
FORMATS = ("table", "json", "csv", "markdown")


def _check_list(value: str) -> list[str]:
    return [c.strip() for c in value.split(",") if c.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="costwatch", description="Find wasted AWS spend, and who left it running."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser(
        "scan",
        help="Scan an AWS account for idle and orphaned resources",
        description=(
            "Scan an AWS account for idle and orphaned resources. Exit codes: 0 done, "
            "1 total waste is at or over --fail-over, 2 error."
        ),
    )

    account = scan_p.add_argument_group("account")
    account.add_argument("--profile", help="AWS profile (default: $AWS_PROFILE or default chain)")
    account.add_argument(
        "--role-arn", help="Assume this IAM role first, e.g. a read-only role in another account"
    )
    account.add_argument("--external-id", help="External ID for --role-arn, if the role needs one")
    account.add_argument(
        "--region",
        action="append",
        dest="regions",
        help="Region to scan; repeat for several (default: all enabled regions)",
    )

    what = scan_p.add_argument_group("what to check")
    what.add_argument(
        "--checks",
        type=_check_list,
        metavar="IDS",
        help="Only run these checks (comma-separated; see `costwatch checks`)",
    )
    what.add_argument(
        "--skip-checks", type=_check_list, metavar="IDS", help="Don't run these checks"
    )
    what.add_argument(
        "--snapshot-age-days",
        type=int,
        default=90,
        help="Flag EBS/RDS snapshots and AMIs older than this (default: 90)",
    )
    what.add_argument(
        "--stopped-days",
        type=int,
        default=30,
        help="Flag instances stopped longer than this (default: 30)",
    )
    what.add_argument(
        "--idle-days",
        type=int,
        default=14,
        help="Days of CloudWatch metrics the idle checks look at (default: 14)",
    )

    extra = scan_p.add_argument_group("context")
    extra.add_argument(
        "--owners",
        action="store_true",
        help=(
            "Find who owns each resource: owner tags, then who created it in CloudTrail "
            "(last 90 days only; slower)"
        ),
    )
    extra.add_argument(
        "--tfstate",
        action="append",
        metavar="LOCATION",
        help=(
            "Terraform state to compare against, marking findings as managed or unmanaged: "
            "a .tfstate file, a directory, s3://bucket/key, or s3://bucket/prefix/ for every "
            "state under it. Repeatable. For other backends: terraform state pull > x.tfstate"
        ),
    )

    filters = scan_p.add_argument_group("filtering")
    filters.add_argument(
        "--min-cost", type=float, default=0.0, help="Hide findings under this $/month"
    )
    filters.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="ID",
        help=f"Ignore a resource by ID, ARN or name (repeatable). Resources tagged "
        f"{IGNORE_TAG}=true are always ignored",
    )
    filters.add_argument(
        "--ignore-file", metavar="PATH", help="File of resource IDs to ignore, one per line"
    )

    output = scan_p.add_argument_group("output")
    output.add_argument("--format", choices=FORMATS, default="table")
    output.add_argument("--json", action="store_true", help="Same as --format json")
    output.add_argument("--output", "-o", metavar="PATH", help="Write the report to a file")
    output.add_argument(
        "--fail-over",
        type=float,
        metavar="DOLLARS",
        help="Exit with code 1 if total waste is at or over this $/month (for CI)",
    )

    sub.add_parser("checks", help="List the available checks")
    sub.add_parser("policy", help="Print the IAM policy costwatch needs")
    return parser


def _session(args: argparse.Namespace) -> boto3.Session:
    session = boto3.Session(profile_name=args.profile)
    if not args.role_arn:
        return session
    params = {"RoleArn": args.role_arn, "RoleSessionName": "costwatch"}
    if args.external_id:
        params["ExternalId"] = args.external_id
    creds = session.client("sts").assume_role(**params)["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=session.region_name,
    )


def _run_scan(args: argparse.Namespace, console: Console, checks: list) -> ScanResult:
    config = ScanConfig(
        snapshot_age_days=args.snapshot_age_days,
        stopped_days=args.stopped_days,
        idle_days=args.idle_days,
    )
    session = _session(args)
    with console.status("Scanning AWS account..."):
        result = scan(session, regions=args.regions, config=config, checks=checks)

    ignore_ids = set(args.ignore)
    if args.ignore_file:
        ignore_ids |= read_ignore_file(args.ignore_file)
    result.findings, result.ignored = apply_ignores(result.findings, ignore_ids)
    result.findings = [f for f in result.findings if f.monthly_cost >= args.min_cost]

    if args.owners:
        with console.status("Looking up owners in CloudTrail..."):
            result.errors.extend(resolve_owners(session, result.findings))
        result.owners_checked = True
    if args.tfstate:
        with console.status("Reading Terraform state..."):
            index, state_errors = load_states(session, args.tfstate)
        result.errors.extend(state_errors)
        annotate(result.findings, index)
        result.iac_checked = True
    return result


def _write(result: ScanResult, fmt: str, path: str | None) -> None:
    if fmt == "table":
        if path:
            with open(path, "w") as f:
                report.print_table(result, Console(file=f, width=160, no_color=True))
        else:
            report.print_table(result, Console())
        return
    render = {"json": report.to_json, "csv": report.to_csv, "markdown": report.to_markdown}
    text = render[fmt](result)
    text = text if text.endswith("\n") else text + "\n"
    if path:
        Path(path).write_text(text)
    else:
        print(text, end="")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "policy":
        print(policy_json())
        return EXIT_OK
    if args.command == "checks":
        width = max(len(i) for i in CHECK_IDS)
        for spec in CHECKS:
            print(f"{spec.id:<{width}}  {spec.description}")
        return EXIT_OK

    console = Console(stderr=True)
    try:
        checks = select_checks(args.checks, args.skip_checks)
    except ValueError as e:
        console.print(f"[red]{e}.[/red] Run `costwatch checks` to list them.")
        return EXIT_ERROR

    try:
        result = _run_scan(args, console, checks)
    except NoCredentialsError:
        console.print("[red]No AWS credentials found.[/red] Set AWS_PROFILE or pass --profile.")
        return EXIT_ERROR
    except (ClientError, BotoCoreError) as e:
        console.print(f"[red]AWS error:[/red] {short_error(e, limit=300)}")
        return EXIT_ERROR
    except OSError as e:
        console.print(f"[red]{e}[/red]")
        return EXIT_ERROR

    fmt = "json" if args.json else args.format
    try:
        _write(result, fmt, args.output)
    except OSError as e:
        console.print(f"[red]Could not write report:[/red] {e}")
        return EXIT_ERROR
    if args.output:
        console.print(f"Report written to {args.output}")

    if args.fail_over is not None and result.total_monthly_cost >= args.fail_over:
        console.print(
            f"[red]Waste of ${result.total_monthly_cost:,.2f}/month is at or over the "
            f"--fail-over limit of ${args.fail_over:,.2f}.[/red]"
        )
        return EXIT_OVER_THRESHOLD
    return EXIT_OK


def run() -> None:
    """Console entry point."""
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    run()
