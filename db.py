"""Conexão com o banco e execução dos scripts SQL do projeto."""

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parent
SQL_DIR = ROOT / "sql"
DEFAULT_DB_PATH = ROOT / "data" / "inventory.db"


def get_engine(url: str | None = None) -> Engine:
    """Engine SQLAlchemy. Usa DATABASE_URL se definida, senão data/inventory.db."""
    url = url or os.environ.get("DATABASE_URL")
    if url is None:
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{DEFAULT_DB_PATH}"
    return create_engine(url)


def run_sql_file(engine: Engine, filename: str) -> None:
    """Executa um script .sql da pasta sql/ (vários comandos, SQLite)."""
    script = (SQL_DIR / filename).read_text(encoding="utf-8")
    raw = engine.raw_connection()
    try:
        raw.driver_connection.executescript(script)
        raw.commit()
    finally:
        raw.close()
