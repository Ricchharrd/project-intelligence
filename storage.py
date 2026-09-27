from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from seed_data import NEWS, PROJECTS


def create_database(database_url: str | None) -> tuple[Engine, bool]:
    persistent = bool(database_url)
    if not database_url:
        data_dir = Path(__file__).resolve().parent / "data"
        data_dir.mkdir(exist_ok=True)
        database_url = f"sqlite:///{data_dir / 'dashboard.db'}"
    elif database_url.startswith(("postgres://", "postgresql://")):
        database_url = "postgresql+psycopg://" + database_url.split("://", 1)[1]
    engine = create_engine(database_url, pool_pre_ping=True)
    bootstrap(engine)
    return engine, persistent


def bootstrap(engine: Engine) -> None:
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(8092701)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                country TEXT NOT NULL,
                aliases TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS news_items (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                published_at TEXT NOT NULL,
                event_date TEXT NOT NULL DEFAULT '',
                topic TEXT NOT NULL DEFAULT '사업동향',
                source_type TEXT NOT NULL,
                source_quality TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'Watch',
                context TEXT NOT NULL DEFAULT '',
                verified_at TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0
            )
        """))
        count = conn.execute(text("SELECT COUNT(*) FROM projects")).scalar_one()
        if count == 0:
            conn.execute(
                text("INSERT INTO projects (id,name,country) VALUES (:id,:name,:country)"),
                [{"id": p[0], "name": p[1], "country": p[2]} for p in PROJECTS],
            )
        count = conn.execute(text("SELECT COUNT(*) FROM news_items")).scalar_one()
        if count == 0:
            columns = ("id","project_id","title","summary","source_name","source_url","published_at","event_date","topic","source_type","source_quality","severity","context")
            rows = [dict(zip(columns, item)) | {"verified_at": "2026-09-22"} for item in NEWS]
            conn.execute(text("""
                INSERT INTO news_items
                (id,project_id,title,summary,source_name,source_url,published_at,event_date,topic,source_type,source_quality,severity,context,verified_at)
                VALUES (:id,:project_id,:title,:summary,:source_name,:source_url,:published_at,:event_date,:topic,:source_type,:source_quality,:severity,:context,:verified_at)
            """), rows)


def projects_frame(engine: Engine) -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT p.*, COUNT(n.id) AS news_count
        FROM projects p LEFT JOIN news_items n ON n.project_id=p.id
        GROUP BY p.id ORDER BY p.active DESC, p.name
    """), engine)


def news_frame(engine: Engine) -> pd.DataFrame:
    return pd.read_sql(text("""
        SELECT n.*, p.name AS project_name, p.country
        FROM news_items n JOIN projects p ON p.id=n.project_id
        ORDER BY n.published_at DESC, n.id DESC
    """), engine)


def save_project(engine: Engine, values: dict[str, Any], project_id: int | None = None) -> None:
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("LOCK TABLE projects IN EXCLUSIVE MODE"))
        if project_id:
            conn.execute(text("UPDATE projects SET name=:name,country=:country,aliases=:aliases,active=:active WHERE id=:id"), values | {"id": project_id})
        else:
            next_id = conn.execute(text("SELECT COALESCE(MAX(id),1000)+1 FROM projects")).scalar_one()
            conn.execute(text("INSERT INTO projects (id,name,country,aliases,active) VALUES (:id,:name,:country,:aliases,:active)"), values | {"id": next_id})


def save_news(engine: Engine, values: dict[str, Any], news_id: int | None = None) -> None:
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("LOCK TABLE news_items IN EXCLUSIVE MODE"))
        if news_id:
            assignments = ",".join(f"{key}=:{key}" for key in values)
            conn.execute(text(f"UPDATE news_items SET {assignments} WHERE id=:id"), values | {"id": news_id})
        else:
            existing = conn.execute(text("SELECT id FROM news_items WHERE project_id=:project_id AND source_url=:source_url"), values).first()
            if existing:
                raise ValueError("이 사업에 같은 원문 URL이 이미 등록되어 있습니다.")
            next_id = conn.execute(text("SELECT COALESCE(MAX(id),2000)+1 FROM news_items")).scalar_one()
            columns = ",".join(["id", *values.keys()])
            binds = ",".join([":id", *(f":{key}" for key in values)])
            conn.execute(text(f"INSERT INTO news_items ({columns}) VALUES ({binds})"), values | {"id": next_id})


def save_approvals(engine: Engine, approvals: dict[int, bool]) -> None:
    if not approvals:
        return
    with engine.begin() as conn:
        conn.execute(text("UPDATE news_items SET approved=:approved WHERE id=:id"), [
            {"id": item_id, "approved": 1 if approved else 0} for item_id, approved in approvals.items()
        ])
