import csv
import io
import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from rich.console import Console
from rich.table import Table

from costwatch import __version__, pricing
from costwatch.models import Finding
from costwatch.scanner import ScanResult


def print_table(result: ScanResult, console: Console | None = None) -> None:
    console = console or Console()
    console.print(f"Account [bold]{result.account_id}[/bold]")

    if not result.findings:
        console.print(f"[green]No waste found across {len(result.regions)} region(s).[/green]")
    else:
        table = Table(title="Wasted AWS spend (estimated)", show_lines=False)
        table.add_column("$/month", justify="right", style="bold")
        table.add_column("Check")
        table.add_column("Region")
        table.add_column("Resource", overflow="fold")
        table.add_column("Details", overflow="fold")
        if result.owners_checked:
            table.add_column("Owner", overflow="fold")
        if result.iac_checked:
            table.add_column("Managed by", overflow="fold")
        for f in result.findings:
            row = [f"{f.monthly_cost:,.2f}", f.check, f.region, _resource_cell(f), f.description]
            if result.owners_checked:
                row.append(_owner_cell(f))
            if result.iac_checked:
                row.append(_iac_cell(f))
            table.add_row(*row)
        console.print(table)

        console.print(_summary(result.findings, "Summary", "Check", lambda f: f.check))
        if result.owners_checked:
            console.print(
                _summary(result.findings, "Waste by owner", "Owner", lambda f: f.owner or "unknown")
            )
        if result.iac_checked:
            console.print(
                _summary(
                    result.findings,
                    "Waste by management",
                    "Managed by",
                    lambda f: f.managed_by or "unmanaged",
                )
            )
        console.print(
            f"[bold]Total: ~${result.total_monthly_cost:,.2f}/month "
            f"(~${result.total_monthly_cost * 12:,.0f}/year)[/bold] "
            f"across {len(result.regions)} region(s)"
        )

    if result.ignored:
        console.print(f"[dim]{result.ignored} finding(s) ignored.[/dim]")
    console.print(f"[dim]{_pricing_note(result)}[/dim]")

    if result.errors:
        console.print(f"\n[yellow]{len(result.errors)} problem(s) during the scan:[/yellow]")
        for error in result.errors:
            console.print(f"  [yellow]•[/yellow] {error}")


def _resource_cell(f: Finding) -> str:
    if not f.name:
        return f.resource_id
    if f.resource_id.startswith("arn:"):
        return f.name  # full ARN is in the JSON/CSV output; it swamps the table
    return f"{f.name}\n[dim]{f.resource_id}[/dim]"


def _owner_cell(f: Finding) -> str:
    if not f.owner:
        return "[dim]unknown[/dim]"
    return f"{f.owner}\n[dim]{f.owner_source}[/dim]"


def _unpriced_regions(result: ScanResult) -> list[str]:
    return [r for r in result.regions if not pricing.has_region(r)]


def _pricing_note(result: ScanResult) -> str:
    note = f"Prices: AWS on-demand list prices of {(pricing.publication_date() or '?')[:10]}"
    if unpriced := _unpriced_regions(result):
        note += f"; no price data for {', '.join(unpriced)}, so us-east-1 prices are used there"
    return note + "."


def _iac_cell(f: Finding) -> str:
    if f.managed_by in (None, "unmanaged"):
        return "[yellow]unmanaged[/yellow]"
    return f"{f.managed_by}\n[dim]{f.iac_address}[/dim]"


def _summary(
    findings: list[Finding], title: str, label: str, key: Callable[[Finding], str]
) -> Table:
    groups: dict[str, list[float]] = defaultdict(list)
    for f in findings:
        groups[key(f)].append(f.monthly_cost)
    table = Table(title=title, show_header=True)
    table.add_column(label)
    table.add_column("Count", justify="right")
    table.add_column("$/month", justify="right")
    for name, costs in sorted(groups.items(), key=lambda kv: -sum(kv[1])):
        table.add_row(name, str(len(costs)), f"{sum(costs):,.2f}")
    return table


SCHEMA_VERSION = 1

CSV_COLUMNS = [
    "monthly_cost",
    "check",
    "region",
    "resource_id",
    "name",
    "description",
    "recommendation",
    "owner",
    "owner_source",
    "managed_by",
    "iac_address",
]


def to_json(result: ScanResult) -> str:
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "costwatch_version": __version__,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "account_id": result.account_id,
            "regions": result.regions,
            "owners_checked": result.owners_checked,
            "iac_checked": result.iac_checked,
            "total_monthly_cost": round(result.total_monthly_cost, 2),
            "ignored": result.ignored,
            "prices_as_of": pricing.publication_date(),
            "regions_priced_as_us_east_1": _unpriced_regions(result),
            "findings": [f.to_dict() for f in result.findings],
            "errors": result.errors,
        },
        indent=2,
    )


def to_csv(result: ScanResult) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for f in result.findings:
        row = f.to_dict()
        row["monthly_cost"] = f"{f.monthly_cost:.2f}"
        writer.writerow(row)
    return out.getvalue()


def _md(text: object) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")


def to_markdown(result: ScanResult) -> str:
    """For pull request comments, issues and wikis."""
    total = result.total_monthly_cost
    lines = [
        f"## AWS waste: ~${total:,.2f}/month (~${total * 12:,.0f}/year)",
        "",
        f"Account `{result.account_id}`, {len(result.regions)} region(s), "
        f"{len(result.findings)} finding(s).",
        "",
    ]
    if result.findings:
        headers = ["$/month", "Check", "Region", "Resource", "Details"]
        if result.owners_checked:
            headers.append("Owner")
        if result.iac_checked:
            headers.append("Managed by")
        lines += ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
        for f in result.findings:
            resource = f"{f.name} (`{f.resource_id}`)" if f.name else f"`{f.resource_id}`"
            row = [f"{f.monthly_cost:,.2f}", f.check, f.region, resource, f.description]
            if result.owners_checked:
                row.append(f.owner or "unknown")
            if result.iac_checked:
                row.append(
                    f"{f.managed_by} `{f.iac_address}`" if f.iac_address else (f.managed_by or "")
                )
            lines.append("| " + " | ".join(_md(c) for c in row) + " |")
        lines.append("")
    if result.ignored:
        lines += [f"{result.ignored} finding(s) ignored.", ""]
    if result.errors:
        lines += [
            f"<details><summary>{len(result.errors)} problem(s) during the scan</summary>",
            "",
        ]
        lines += [f"- {_md(e)}" for e in result.errors]
        lines += ["", "</details>", ""]
    lines.append(f"_{_pricing_note(result)} Generated by costwatch {__version__}._")
    return "\n".join(lines) + "\n"
