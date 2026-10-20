"""Accès SQLite et migrations versionnées.

Pas d'ORM: sqlite3 de la stdlib. Les migrations sont de simples fichiers
`NNN_nom.sql` appliqués dans l'ordre, une fois chacun.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: l'API web ouvre une connexion par requête, mais
    # FastAPI exécute les endpoints synchrones et les tâches de fond dans des
    # threads différents de celui qui a créé l'objet.
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: un lecteur ne bloque plus un écrivain (le front interroge l'état
    # d'une tâche pendant que celle-ci écrit des cartes).
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def migrate(conn: sqlite3.Connection) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}

    newly_applied = []
    pending = [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name not in applied]
    if not pending:
        return newly_applied

    # Les migrations qui reconstruisent une table (CREATE new / DROP old /
    # RENAME) déclencheraient les ON DELETE CASCADE des tables qui la
    # référencent: le DROP effacerait l'historique des révisions. On coupe
    # donc les FK le temps des migrations, comme recommandé par SQLite, puis
    # on vérifie l'intégrité avant de valider.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for path in pending:
            conn.executescript(path.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (path.name,))
            conn.commit()
            newly_applied.append(path.name)

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Migration incohérente, clés étrangères orphelines: {violations}")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    return newly_applied


def open_db(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    migrate(conn)
    return conn


def ensure_cours(conn: sqlite3.Connection, cours: str) -> None:
    conn.execute("INSERT OR IGNORE INTO cours (nom) VALUES (?)", (cours,))
