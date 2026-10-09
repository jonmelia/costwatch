"""costwatch: find wasted AWS spend, and who left it running."""

from importlib.metadata import version

__version__ = version("costwatch")

from costwatch.models import Finding, ScanConfig  # noqa: E402
from costwatch.scanner import ScanResult, scan  # noqa: E402

__all__ = ["Finding", "ScanConfig", "ScanResult", "__version__", "scan"]
