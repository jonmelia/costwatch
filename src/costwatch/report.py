import json
from collections import defaultdict

from rich.console import Console
from rich.table import Table

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
        for f in result.findings:
            resource = f"{f.name}\n[dim]{f.resource_id}[/dim]" if f.name else f.resource_id
            table.add_row(f"{f.monthly_cost:,.2f}", f.check, f.region, resource, f.description)
        console.print(table)

        by_check: dict[str, list[float]] = defaultdict(list)
        for f in result.findings:
            by_check[f.check].append(f.monthly_cost)
        summary = Table(title="Summary", show_header=True)
        summary.add_column("Check")
        summary.add_column("Count", justify="right")
        summary.add_column("$/month", justify="right")
        for check, costs in sorted(by_check.items(), key=lambda kv: -sum(kv[1])):
            summary.add_row(check, str(len(costs)), f"{sum(costs):,.2f}")
        console.print(summary)
        console.print(
            f"[bold]Total: ~${result.total_monthly_cost:,.2f}/month "
            f"(~${result.total_monthly_cost * 12:,.0f}/year)[/bold] "
            f"across {len(result.regions)} region(s)"
        )

    if result.errors:
        console.print(f"\n[yellow]{len(result.errors)} check(s) could not run:[/yellow]")
        for error in result.errors:
            console.print(f"  [yellow]•[/yellow] {error}")


def to_json(result: ScanResult) -> str:
    return json.dumps(
        {
            "account_id": result.account_id,
            "regions": result.regions,
            "total_monthly_cost": round(result.total_monthly_cost, 2),
            "findings": [f.to_dict() for f in result.findings],
            "errors": result.errors,
        },
        indent=2,
    )
