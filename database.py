"""
database.py
Simple SQLite persistence for analysis results.
Each project analysis is stored as a single JSON blob.
"""

import sqlite3
import json
import os
from typing import Optional, Dict, Any, List

DB_PATH = os.path.join(os.path.dirname(__file__), "projectdna.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                result_json TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                review_mode TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                review_json TEXT NOT NULL,
                FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE
            )
        """)
        conn.commit()


def save_analysis(project_name: str, result: Dict[str, Any]) -> int:
    with _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO analyses (project_name, result_json) VALUES (?, ?)",
            (project_name, json.dumps(result, default=str)),
        )
        conn.commit()
        return cur.lastrowid


def load_analysis(analysis_id: int) -> Optional[Dict[str, Any]]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "project_name": row["project_name"],
        "created_at": row["created_at"],
        "result": json.loads(row["result_json"]),
    }


def list_analyses() -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, project_name, created_at FROM analyses ORDER BY id DESC"
        ).fetchall()
    return [{"id": r["id"], "project_name": r["project_name"], "created_at": r["created_at"]} for r in rows]


def delete_analysis(analysis_id: int):
    with _get_conn() as conn:
        conn.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
        conn.commit()


def list_analyses_for_project(project_name: str) -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id, project_name, created_at FROM analyses WHERE project_name = ? ORDER BY id DESC",
            (project_name,),
        ).fetchall()
    return [{"id": r["id"], "project_name": r["project_name"], "created_at": r["created_at"]} for r in rows]


def list_projects() -> List[Dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT project_name, COUNT(*) as snapshot_count, MAX(created_at) as latest_created_at, MAX(id) as latest_id
            FROM analyses
            GROUP BY project_name
            ORDER BY latest_created_at DESC
        """).fetchall()
    return [
        {
            "project_name": r["project_name"],
            "snapshot_count": r["snapshot_count"],
            "latest_created_at": r["latest_created_at"],
            "latest_id": r["latest_id"],
        }
        for r in rows
    ]


def save_ai_review(analysis_id: int, review_data: Dict[str, Any]) -> int:
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO ai_reviews (analysis_id, provider, review_mode, review_json)
               VALUES (?, ?, ?, ?)""",
            (
                analysis_id,
                review_data.get("provider", "unknown"),
                review_data.get("review_mode", "auto"),
                json.dumps(review_data, default=str),
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_latest_ai_review(analysis_id: int) -> Optional[Dict[str, Any]]:
    with _get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM ai_reviews WHERE analysis_id = ? ORDER BY id DESC LIMIT 1""",
            (analysis_id,),
        ).fetchone()
    if row is None:
        return None
    data = json.loads(row["review_json"])
    data["id"] = row["id"]
    data["created_at"] = row["created_at"]
    return data
