"""SQLite persistence. Standard library only.

Design points that matter in an interview:
- `faxes` has UNIQUE constraints on the content hash and on the source key, so
  idempotency is enforced by the database even if two requests race.
- `audit_log` is append-only: triggers abort any UPDATE or DELETE on it.
- `audit_log.detail` only ever holds keys from audit.ALLOWED_DETAIL_KEYS.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS faxes (
  fax_id        TEXT PRIMARY KEY,
  content_hash  TEXT NOT NULL UNIQUE,
  source        TEXT NOT NULL,
  source_key    TEXT UNIQUE,
  received_at   TEXT NOT NULL,
  page_count    INTEGER,
  status        TEXT NOT NULL,           -- received | processed | dead_letter
  blob          BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
  doc_id        TEXT PRIMARY KEY,
  fax_id        TEXT NOT NULL REFERENCES faxes(fax_id),
  doc_type      TEXT NOT NULL,
  denial_reason TEXT,
  queue         TEXT NOT NULL,
  tentative_queue TEXT,
  priority      TEXT NOT NULL DEFAULT 'normal',
  needs_review  INTEGER NOT NULL DEFAULT 0,
  review_reasons TEXT NOT NULL DEFAULT '[]',
  extraction    TEXT NOT NULL,           -- JSON, includes per-field confidence
  rule_hits     TEXT NOT NULL,           -- JSON list of {rule_id, detail}
  state         TEXT NOT NULL,           -- open | resolved
  created_at    TEXT NOT NULL,
  resolved_at   TEXT
);
CREATE TABLE IF NOT EXISTS actions (
  action_id     TEXT PRIMARY KEY,
  doc_id        TEXT NOT NULL REFERENCES documents(doc_id),
  kind          TEXT NOT NULL,
  draft         TEXT NOT NULL,
  status        TEXT NOT NULL,           -- pending_approval | approved | rejected | dispatched
  approved_by   TEXT,
  created_at    TEXT NOT NULL,
  decided_at    TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
  outbox_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  action_id     TEXT NOT NULL REFERENCES actions(action_id),
  channel       TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dead_letter (
  dl_id         TEXT PRIMARY KEY,
  fax_id        TEXT NOT NULL REFERENCES faxes(fax_id),
  stage         TEXT NOT NULL,
  error_class   TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  state         TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS audit_log (
  seq           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  event         TEXT NOT NULL,
  fax_id        TEXT,
  doc_id        TEXT,
  actor         TEXT NOT NULL,
  detail        TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._lock = threading.RLock()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        r = self.rows(sql, args)
        return r[0] if r else None

    # ---- audit (the only writer; no update/delete exists)
    def append_audit(self, conn: sqlite3.Connection, event: str, fax_id: str | None, doc_id: str | None, actor: str, detail: dict) -> None:
        conn.execute(
            "INSERT INTO audit_log (ts, event, fax_id, doc_id, actor, detail) VALUES (?,?,?,?,?,?)",
            (now_iso(), event, fax_id, doc_id, actor, json.dumps(detail, sort_keys=True)),
        )
