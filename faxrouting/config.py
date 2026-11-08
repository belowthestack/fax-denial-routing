"""Environment-driven wiring. No secrets live in code; AWS credentials come from the standard boto3 chain."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Callable

from .extractors import (
    BedrockVisionExtractor,
    ConsensusExtractor,
    Extractor,
    LocalTextExtractor,
    ReplayExtractor,
)

ROOT = Path(__file__).resolve().parents[1]
CAPTURED = ROOT / "fixtures" / "bedrock_captured"
PRIMARY_MODEL = os.environ.get("BEDROCK_PRIMARY_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
SECONDARY_MODEL = os.environ.get("BEDROCK_SECONDARY_MODEL", "us.anthropic.claude-sonnet-4-6")


def build_extractor(kind: str | None = None) -> Extractor:
    kind = kind or os.environ.get("FAX_BACKEND", "replay")
    if kind == "bedrock":
        region = os.environ.get("AWS_REGION", "us-east-1")
        return ConsensusExtractor(BedrockVisionExtractor(PRIMARY_MODEL, region), BedrockVisionExtractor(SECONDARY_MODEL, region))
    if kind == "replay":
        return ConsensusExtractor(ReplayExtractor(CAPTURED / "haiku-4-5"), ReplayExtractor(CAPTURED / "sonnet-4-6"))
    if kind == "local":
        return LocalTextExtractor()
    raise ValueError(f"unknown FAX_BACKEND {kind!r}")


def build_alert() -> Callable[[str], None]:
    """Posts to ALERT_WEBHOOK_URL if set (Slack-style JSON body); otherwise prints to stderr."""
    url = os.environ.get("ALERT_WEBHOOK_URL")

    def alert(message: str) -> None:
        if not url:
            import sys

            print(f"ALERT: {message}", file=sys.stderr)
            return
        req = urllib.request.Request(url, data=json.dumps({"text": message}).encode(), headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)

    return alert
