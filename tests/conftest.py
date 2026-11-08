from __future__ import annotations

import json
from pathlib import Path

import pytest

from faxrouting.extractors import ConsensusExtractor, ReplayExtractor
from faxrouting.pipeline import Pipeline
from faxrouting.store import Store

ROOT = Path(__file__).resolve().parents[1]
LETTERS = ROOT / "fixtures" / "letters"
CAPTURED = ROOT / "fixtures" / "bedrock_captured"

# These three were misread by one model or unreadable on the page. The consensus
# check must send them to a person, and the tests assert that it does.
EXPECT_REVIEW = {
    "02_approved_scanned_blurry",
    "05_denial_eligibility",
    "07_denial_coding_scanned",
}


def load_letters() -> dict[str, tuple[bytes, dict]]:
    return {p.stem: (p.read_bytes(), json.loads(p.with_suffix(".expected.json").read_text())) for p in sorted(LETTERS.glob("*.pdf"))}


@pytest.fixture(scope="session")
def letters() -> dict[str, tuple[bytes, dict]]:
    return load_letters()


@pytest.fixture()
def extractor() -> ConsensusExtractor:
    return ConsensusExtractor(ReplayExtractor(CAPTURED / "haiku-4-5"), ReplayExtractor(CAPTURED / "sonnet-4-6"))


@pytest.fixture()
def pipeline(extractor) -> Pipeline:
    alerts: list[str] = []
    p = Pipeline(Store(), extractor, alert=alerts.append)
    p.alerts = alerts  # type: ignore[attr-defined]
    return p
