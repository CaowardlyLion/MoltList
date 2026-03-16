import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "moltlist.db"


def dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {col["name"] for col in columns}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('agent','human')),
                can_offer_services INTEGER NOT NULL DEFAULT 0,
                can_hire_agents INTEGER NOT NULL DEFAULT 0,
                wallet_address TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS agent_profiles (
                user_id INTEGER PRIMARY KEY,
                headline TEXT NOT NULL,
                skills_json TEXT NOT NULL,
                models_json TEXT NOT NULL,
                hourly_rate_usdc REAL NOT NULL,
                bio TEXT NOT NULL,
                availability_status TEXT NOT NULL DEFAULT 'available',
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS contracts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contractor_id INTEGER NOT NULL,
                hired_agent_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                stablecoin TEXT NOT NULL,
                budget_amount REAL NOT NULL,
                x402_payment_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','active','in_review','completed','cancelled')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(contractor_id) REFERENCES users(id),
                FOREIGN KEY(hired_agent_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER NOT NULL,
                rater_id INTEGER NOT NULL,
                rated_id INTEGER NOT NULL,
                stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
                comment TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(contract_id, rater_id),
                FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE,
                FOREIGN KEY(rater_id) REFERENCES users(id),
                FOREIGN KEY(rated_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS payment_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER NOT NULL,
                payer_id INTEGER NOT NULL,
                payee_id INTEGER NOT NULL,
                stablecoin TEXT NOT NULL,
                amount REAL NOT NULL,
                x402_payment_url TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('processing','paid','failed')),
                tx_ref TEXT,
                protocol_response_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE,
                FOREIGN KEY(payer_id) REFERENCES users(id),
                FOREIGN KEY(payee_id) REFERENCES users(id)
            );
            """
        )

        _ensure_column(conn, "contracts", "payment_status", "TEXT NOT NULL DEFAULT 'unpaid'")
        _ensure_column(conn, "contracts", "last_payment_execution_id", "INTEGER")


def decode_json_array(value: str) -> list[str]:
    return json.loads(value)


def encode_json_array(value: list[str]) -> str:
    return json.dumps(value)
