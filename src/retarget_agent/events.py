"""SQLite append-only event index; large artifacts remain in the filesystem."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import ReviewEvent, RunManifest, StageEvent


class SqliteEventStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_manifest_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_run_manifest_run
                    ON run_manifest_events(run_id, sequence);

                CREATE TABLE IF NOT EXISTS stage_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    task_id TEXT,
                    candidate_id TEXT,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    reviewer_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    grade TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    supersedes_event_id TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reviews_run_task
                    ON review_events(run_id, task_id, created_at);
                """
            )

    def append_run(self, manifest: RunManifest) -> None:
        payload = json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO run_manifest_events(run_id, created_at, payload_json)
                VALUES (?, ?, ?)
                """,
                (manifest.run_id, manifest.created_at.isoformat(), payload),
            )

    def append_stage(self, event: StageEvent) -> None:
        payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO stage_events(
                    event_id, run_id, task_id, candidate_id, stage, status, started_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.run_id,
                    event.task_id,
                    event.candidate_id,
                    event.stage,
                    event.status,
                    event.started_at.isoformat(),
                    payload,
                ),
            )

    def append_review(self, event: ReviewEvent) -> None:
        payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO review_events(
                    event_id, run_id, reviewer_id, task_id, candidate_id, grade,
                    created_at, supersedes_event_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.run_id,
                    event.reviewer_id,
                    event.task_id,
                    event.candidate_id,
                    event.grade.value,
                    event.created_at.isoformat(),
                    event.supersedes_event_id,
                    payload,
                ),
            )

    def review_events(self, run_id: str) -> list[ReviewEvent]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM review_events
                WHERE run_id = ? ORDER BY created_at, event_id
                """,
                (run_id,),
            ).fetchall()
        return [ReviewEvent.model_validate_json(row[0]) for row in rows]

    def stage_events(self, run_id: str) -> list[StageEvent]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM stage_events
                WHERE run_id = ? ORDER BY started_at, event_id
                """,
                (run_id,),
            ).fetchall()
        return [StageEvent.model_validate_json(row[0]) for row in rows]
