"""AFE pipeline tracker — minimal SQLite-backed state machine for in-flight AFEs.

Designed to demonstrate pipeline visibility without a real ERP integration.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd


Status = Literal["draft", "engineering_review", "finance_review", "approved", "executed", "rejected"]
STATUS_ORDER = ["draft", "engineering_review", "finance_review", "approved", "executed"]

# Typical days per stage (used for bottleneck prediction)
STAGE_SLA_DAYS = {
    "draft": 2,
    "engineering_review": 5,
    "finance_review": 8,
    "approved": 3,
    "executed": None,
}


@dataclass
class AFERecord:
    afe_number: str
    well_id: str
    intervention: str
    total_cost_usd: float
    status: Status
    created_date: str
    last_updated: str
    rig_name: str | None = None
    requested_by: str | None = None
    notes: str | None = None


class AFETracker:
    def __init__(self, db_path: str | Path = "pipeline.sqlite"):
        self.db_path = Path(db_path)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS afes (
                    afe_number TEXT PRIMARY KEY,
                    well_id TEXT NOT NULL,
                    intervention TEXT NOT NULL,
                    total_cost_usd REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_date TEXT NOT NULL,
                    last_updated TEXT NOT NULL,
                    rig_name TEXT,
                    requested_by TEXT,
                    notes TEXT
                )
            """)

    def upsert(self, rec: AFERecord) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO afes (afe_number, well_id, intervention, total_cost_usd,
                                  status, created_date, last_updated, rig_name, requested_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(afe_number) DO UPDATE SET
                    status=excluded.status,
                    total_cost_usd=excluded.total_cost_usd,
                    last_updated=excluded.last_updated,
                    notes=excluded.notes
            """, (rec.afe_number, rec.well_id, rec.intervention, rec.total_cost_usd,
                  rec.status, rec.created_date, rec.last_updated,
                  rec.rig_name, rec.requested_by, rec.notes))

    def advance(self, afe_number: str, to_status: Status, note: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute("""
                UPDATE afes SET status = ?, last_updated = ?, notes = COALESCE(?, notes)
                WHERE afe_number = ?
            """, (to_status, date.today().isoformat(), note, afe_number))

    def as_dataframe(self) -> pd.DataFrame:
        with self._conn() as conn:
            df = pd.read_sql("SELECT * FROM afes ORDER BY created_date DESC", conn)
        if df.empty:
            return df
        df["last_updated"] = pd.to_datetime(df["last_updated"])
        df["created_date"] = pd.to_datetime(df["created_date"])
        df["days_in_status"] = (pd.Timestamp.now().normalize() - df["last_updated"]).dt.days
        df["days_open"] = (pd.Timestamp.now().normalize() - df["created_date"]).dt.days
        df["bottleneck_risk"] = df.apply(self._risk, axis=1)
        return df

    @staticmethod
    def _risk(row) -> str:
        sla = STAGE_SLA_DAYS.get(row["status"])
        if sla is None or row["status"] in ("executed", "approved"):
            return "—"
        if row["days_in_status"] > sla * 1.5:
            return "HIGH"
        if row["days_in_status"] > sla:
            return "MEDIUM"
        return "LOW"


def seed_demo_data(db_path: str | Path = "pipeline.sqlite") -> None:
    """Populate the tracker with 12 fake AFEs spanning the status pipeline."""
    tracker = AFETracker(db_path)
    today = date.today()

    rows = [
        ("AFE-2026-0042", "ED-001H", "acid_stimulation",       210_000, "engineering_review",  9, "Rig 03"),
        ("AFE-2026-0043", "ED-002H", "esp_swap",               340_000, "finance_review",     15, "Rig 07"),
        ("AFE-2026-0044", "ED-005H", "gas_separator",          135_000, "draft",                1, "Rig 02"),
        ("AFE-2026-0045", "ED-008H", "scale_treatment",         92_000, "approved",             3, "Rig 03"),
        ("AFE-2026-0046", "ED-012H", "esp_to_beam_conversion", 305_000, "engineering_review",  12, "Rig 09"),
        ("AFE-2026-0047", "ED-014H", "rod_pump_workover",       58_000, "executed",            21, "Rig 11"),
        ("AFE-2026-0048", "ED-017H", "gas_lift_optimization",   22_000, "finance_review",       4, "Rig 06"),
        ("AFE-2026-0049", "ED-019H", "paraffin_treatment",      17_000, "approved",             1, "Rig 04"),
        ("AFE-2026-0050", "ED-020H", "p_and_a",                238_000, "draft",                5, "Rig 11"),
        ("AFE-2026-0051", "ED-022H", "acid_stimulation",       195_000, "executed",            14, "Rig 03"),
        ("AFE-2026-0052", "ED-024H", "esp_swap",               365_000, "rejected",            10, "Rig 09"),
        ("AFE-2026-0053", "ED-025H", "scale_treatment",         88_000, "engineering_review",   2, "Rig 02"),
    ]
    for afe_no, well, interv, cost, status, days_old, rig in rows:
        created = (today - timedelta(days=days_old + 2)).isoformat()
        updated = (today - timedelta(days=days_old)).isoformat()
        tracker.upsert(AFERecord(
            afe_number=afe_no, well_id=well, intervention=interv,
            total_cost_usd=cost, status=status,
            created_date=created, last_updated=updated,
            rig_name=rig, requested_by="Senior PE",
        ))


if __name__ == "__main__":
    seed_demo_data()
    print("Seeded 12 AFEs into pipeline.sqlite")
    df = AFETracker().as_dataframe()
    print(df.to_string(index=False))
