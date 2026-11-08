"""Where faxes come from. Every source feeds the same `Pipeline.ingest`.

FolderSource   Reads PDFs from a directory. This is what the demo and tests use.
FaxageSource   Adapter for the Faxage HTTP API. PLACEHOLDER: the Faxage trial this
               was built against expired, so the adapter is not exercised live and
               only its response parser is unit-tested. Faxage also does not sign a
               BAA, so a production deployment needs a BAA-covered fax provider
               behind this same interface.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from .pipeline import IngestResult, Pipeline


class FaxSource(Protocol):
    name: str

    def pending(self) -> Iterable[tuple[str, bytes]]: ...


@dataclass
class FolderSource:
    directory: Path
    name: str = "folder"

    def pending(self) -> Iterable[tuple[str, bytes]]:
        for p in sorted(Path(self.directory).glob("*.pdf")):
            yield p.name, p.read_bytes()


def parse_listfax(body: str) -> list[dict[str, str]]:
    """Parse Faxage `listfax` output: tab-separated `faxid, datetime, sender, destination` lines."""
    rows = []
    for line in body.splitlines():
        parts = line.split("\t")
        if len(parts) >= 4 and parts[0].strip():
            rows.append({"faxid": parts[0].strip(), "received": parts[1], "sender": parts[2], "destination": parts[3]})
    return rows


@dataclass
class FaxageSource:
    username: str
    company: str
    password: str
    url: str = "https://api.faxage.com/httpsfax.php"
    name: str = "faxage"

    def _post(self, **fields: str) -> bytes:
        import requests

        r = requests.post(self.url, data={"username": self.username, "company": self.company, "password": self.password, **fields}, timeout=30)
        r.raise_for_status()
        if r.content.startswith(b"ERR"):
            raise RuntimeError(r.content[:40].decode("ascii", "replace"))
        return r.content

    def pending(self) -> Iterable[tuple[str, bytes]]:
        for row in parse_listfax(self._post(operation="listfax", unhandled="1").decode("utf-8", "replace")):
            # Never mark faxes handled on the provider side: our own idempotency ledger is the authority.
            yield row["faxid"], self._post(operation="getfax", faxid=row["faxid"])


def poll(source: FaxSource, pipeline: Pipeline) -> list[IngestResult]:
    """One polling pass. Safe to run on any schedule, including overlapping runs."""
    return [pipeline.ingest(data, source.name, source_id) for source_id, data in source.pending()]
