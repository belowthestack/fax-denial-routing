"""Capture real Bedrock extraction payloads for every fixture (regression fixtures).

Needs AWS credentials in the normal boto3 chain (environment variables, profile,
or role) and Bedrock access to the chosen model. Writes
fixtures/bedrock_captured/<label>/<fixture>.json containing the untouched tool input the
model returned, the token usage, the model id and the document hash. The test
suite replays these files, so it never needs the network.

Usage: python scripts/capture_bedrock.py [--model MODEL_ID] [--only SUBSTRING]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from faxrouting.extractors import DEFAULT_MODEL, BedrockVisionExtractor, content_hash  # noqa: E402

LETTERS = ROOT / "fixtures" / "letters"
OUT = ROOT / "fixtures" / "bedrock_captured"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--label", default="haiku-4-5", help="subfolder name under fixtures/bedrock_captured")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    out = OUT / args.label
    out.mkdir(parents=True, exist_ok=True)
    bedrock = BedrockVisionExtractor(model_id=args.model)
    for pdf in sorted(LETTERS.glob("*.pdf")):
        if args.only not in pdf.stem:
            continue
        data = pdf.read_bytes()
        tool_input, usage, pages = bedrock.call(data)
        payload = {
            "fixture": pdf.stem,
            "content_sha256": content_hash(data),
            "model_id": args.model,
            "captured_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "page_count": pages,
            "usage": {k: usage.get(k) for k in ("inputTokens", "outputTokens", "totalTokens")},
            "tool_input": tool_input,
        }
        (out / f"{pdf.stem}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"captured {pdf.stem}: {payload['usage']}")


if __name__ == "__main__":
    main()
