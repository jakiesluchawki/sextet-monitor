"""GitHub's public incident metadata; no credentials or full updates."""
from __future__ import annotations

from monitor.contracts import Fetcher, FetchedDocument, ProviderBatch
from . import statuspage

SOURCE_ID = "github_status"
URL = statuspage.endpoint(SOURCE_ID)


def parse(doc: FetchedDocument) -> ProviderBatch:
    return statuspage.parse(doc, source_id=SOURCE_ID)


async def collect(fetcher: Fetcher, config: dict[str, str]) -> ProviderBatch:
    return await statuspage.collect(fetcher, source_id=SOURCE_ID)
