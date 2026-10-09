import json
from collections import defaultdict
from collections.abc import Callable

from rich.console import Console
from rich.table import Table

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
            resource = f"{f.name}\n[dim]{f.resource_id}[/dim]" if f.name else f.resource_id
            row = [f"{f.monthly_cost:,.2f}", f.check, f.region, resource, f.description]
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

    if result.errors:
        console.print(f"\n[yellow]{len(result.errors)} problem(s) during the scan:[/yellow]")
        for error in result.errors:
            console.print(f"  [yellow]•[/yellow] {error}")


def _owner_cell(f: Finding) -> str:
    if not f.owner:
        return "[dim]unknown[/dim]"
    return f"{f.owner}\n[dim]{f.owner_source}[/dim]"


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


def to_json(result: ScanResult) -> str:
    return json.dumps(
        {
            "account_id": result.account_id,
            "regions": result.regions,
            "owners_checked": result.owners_checked,
            "iac_checked": result.iac_checked,
            "total_monthly_cost": round(result.total_monthly_cost, 2),
            "findings": [f.to_dict() for f in result.findings],
            "errors": result.errors,
        },
        indent=2,
    )
