import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "question_bank.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            answer TEXT NOT NULL,
            difficulty TEXT NOT NULL,
            topic TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            content TEXT NOT NULL,
            ollama_context TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    document_columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "ollama_context" not in document_columns:
        conn.execute("ALTER TABLE documents ADD COLUMN ollama_context TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS generation_cache (
            cache_key TEXT PRIMARY KEY,
            questions_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()
