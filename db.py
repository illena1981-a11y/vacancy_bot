"""SQLite: подписки на запросы и список уже показанных вакансий."""
import os
import sqlite3
import time

DB_PATH = os.getenv("DB_PATH", "vacancies.db")

_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript("""
CREATE TABLE IF NOT EXISTS subs(
    chat_id INTEGER PRIMARY KEY,
    query   TEXT    NOT NULL,
    remote  INTEGER NOT NULL,
    use_ex  INTEGER NOT NULL,
    days    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS seen(
    chat_id INTEGER NOT NULL,
    key     TEXT    NOT NULL,
    ts      INTEGER NOT NULL,
    PRIMARY KEY (chat_id, key)
);
""")


def add_sub(chat_id, query, remote, use_ex, days):
    with _conn:
        _conn.execute("INSERT OR REPLACE INTO subs VALUES (?,?,?,?,?)",
                      (chat_id, query, int(remote), int(use_ex), days))


def remove_sub(chat_id) -> bool:
    with _conn:
        cur = _conn.execute("DELETE FROM subs WHERE chat_id=?", (chat_id,))
        _conn.execute("DELETE FROM seen WHERE chat_id=?", (chat_id,))
    return cur.rowcount > 0


def get_sub(chat_id):
    return _conn.execute("SELECT * FROM subs WHERE chat_id=?", (chat_id,)).fetchone()


def all_subs():
    return _conn.execute("SELECT * FROM subs").fetchall()


def _keys(v):
    # по ссылке (та же вакансия) и по «название+компания» (дубль на другой площадке)
    return ("u:" + v.url, "k:" + v.key)


def filter_unseen(chat_id, vacancies):
    seen = {r["key"] for r in _conn.execute("SELECT key FROM seen WHERE chat_id=?", (chat_id,))}
    return [v for v in vacancies if not any(k in seen for k in _keys(v))]


def mark_seen(chat_id, vacancies):
    now = int(time.time())
    with _conn:
        _conn.executemany(
            "INSERT OR IGNORE INTO seen VALUES (?,?,?)",
            [(chat_id, k, now) for v in vacancies for k in _keys(v)],
        )


def prune(days=90):
    with _conn:
        _conn.execute("DELETE FROM seen WHERE ts < ?", (int(time.time()) - days * 86400,))
