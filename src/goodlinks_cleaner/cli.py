"""``glc`` command line interface."""

from __future__ import annotations

import typer
from rich.console import Console

from goodlinks_cleaner import __version__
from goodlinks_cleaner.api import GoodLinksClient, GoodLinksError
from goodlinks_cleaner.config import Settings

app = typer.Typer(
    help="Clean up and triage a GoodLinks library via its local HTTP API.",
    no_args_is_help=True,
)
console = Console()
err = Console(stderr=True)


def client() -> GoodLinksClient:
    s = Settings.load()
    return GoodLinksClient(s.api_url, s.api_token)


def fail(msg: str) -> None:
    err.print(f"[red]error:[/red] {msg}")
    raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print the version."""
    console.print(__version__)


@app.command()
def ping() -> None:
    """Check that the GoodLinks API is reachable and the token works."""
    s = Settings.load()
    try:
        with GoodLinksClient(s.api_url, s.api_token) as c:
            lists = c.lists()
            tags = c.tags()
    except GoodLinksError as e:
        fail(str(e))
    console.print(f"[green]ok[/green] {s.api_url}: {len(lists)} lists, {len(tags)} tags")
    console.print(f"cache: {s.db_path}")


if __name__ == "__main__":
    app()
