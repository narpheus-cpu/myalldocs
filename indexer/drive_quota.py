from __future__ import annotations

from dataclasses import dataclass


class DriveQuotaPaused(RuntimeError):
    """Stops the run before configured free-use safety budgets are exceeded."""


@dataclass
class DriveUsage:
    quota_units: int = 0
    downloaded_bytes: int = 0


class DriveQuotaGuard:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.usage = DriveUsage()

    def consume_units(self, units: int) -> None:
        limit = int(self.config.get("maxQuotaUnitsPerRun", 0) or 0)
        if limit and self.usage.quota_units + units > limit:
            raise DriveQuotaPaused("Drive free-use quota-unit safety budget reached")
        self.usage.quota_units += units

    def consume_operation(self, operation: str) -> None:
        costs = self.config.get("unitCosts", {})
        if operation not in costs:
            raise DriveQuotaPaused(f"Drive quota cost is not configured for {operation}; zero-cost mode stops safely")
        self.consume_units(int(costs[operation]))

    def reserve_download(self, expected_bytes: int | None) -> None:
        limit = int(self.config.get("maxDownloadBytesPerRun", 0) or 0)
        if limit and (expected_bytes is None or expected_bytes < 0):
            raise DriveQuotaPaused("Drive file size is unknown; zero-cost mode refuses an unbounded download")
        if limit and self.usage.downloaded_bytes + expected_bytes > limit:
            raise DriveQuotaPaused("Drive free-use download safety budget reached")
        self.usage.downloaded_bytes += max(0, expected_bytes or 0)
