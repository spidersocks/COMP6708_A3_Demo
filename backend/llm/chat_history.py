"""Per-session chat history for multi-turn context (SQLite + memory).

Ports the unified_backend DynamoDB history behaviour without AWS:
- Keep the last N messages (~5 turns ≈ 10 messages) for prompt context
- Persist across process restarts via SQLite (important on SAE redeploys)
- Always enabled for web: sessions (demo UI) — unlike production WhatsApp path
"""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Dict, List, Optional

from config import SETTINGS

_lock = threading.Lock()
_MEM: Dict[str, List[Dict]] = {}
_DB_READY = False


def _log(msg: str) -> None:
    print(f"[CHAT_HISTORY] {msg}", flush=True)


def _db_path():
    return SETTINGS.chat_history_db


def _ensure_db() -> None:
    global _DB_READY
    if _DB_READY:
        return
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_history (
                session_id TEXT NOT NULL,
                ts INTEGER NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                lang TEXT,
                PRIMARY KEY (session_id, ts)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_session_ts ON chat_history(session_id, ts)"
        )
        conn.commit()
    _DB_READY = True
    _log(f"SQLite ready at {path}")


def save_message(
    session_id: str,
    role: str,
    message: str,
    lang: Optional[str] = None,
    timestamp: Optional[float] = None,
) -> None:
    if not session_id:
        return
    ts = int(timestamp or time.time())
    # Avoid PK collisions when saving user+bot in the same second
    item = {
        "session_id": session_id,
        "ts": ts,
        "role": role,
        "message": message or "",
        "lang": lang,
    }
    with _lock:
        try:
            _ensure_db()
            # Bump ts if collision
            with sqlite3.connect(str(_db_path())) as conn:
                while True:
                    row = conn.execute(
                        "SELECT 1 FROM chat_history WHERE session_id=? AND ts=?",
                        (session_id, item["ts"]),
                    ).fetchone()
                    if not row:
                        break
                    item["ts"] += 1
                conn.execute(
                    "INSERT INTO chat_history(session_id, ts, role, message, lang) VALUES (?,?,?,?,?)",
                    (item["session_id"], item["ts"], item["role"], item["message"], item["lang"]),
                )
                conn.commit()
        except Exception as e:
            _log(f"SQLite save failed ({type(e).__name__}: {e}); using memory")
            bucket = _MEM.setdefault(session_id, [])
            while any(x["ts"] == item["ts"] for x in bucket):
                item["ts"] += 1
            bucket.append(dict(item))
            bucket.sort(key=lambda x: x["ts"])
            _MEM[session_id] = bucket[-50:]


def get_recent_history(
    session_id: str,
    limit: Optional[int] = None,
    oldest_first: bool = True,
) -> List[Dict]:
    if not session_id:
        return []
    limit = int(limit if limit is not None else SETTINGS.chat_history_limit)
    with _lock:
        try:
            _ensure_db()
            with sqlite3.connect(str(_db_path())) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT session_id, ts, role, message, lang
                    FROM chat_history
                    WHERE session_id = ?
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
            items = [dict(r) for r in rows]
            items.sort(key=lambda x: x["ts"], reverse=not oldest_first)
            if oldest_first:
                items = sorted(items, key=lambda x: x["ts"])
            return items
        except Exception as e:
            _log(f"SQLite read failed ({type(e).__name__}: {e}); using memory")
            items = list(_MEM.get(session_id, []))
            items = sorted(items, key=lambda x: x["ts"], reverse=True)[:limit]
            return list(sorted(items, key=lambda x: x["ts"]))


def prune_history(session_id: str, keep: Optional[int] = None) -> None:
    if not session_id:
        return
    keep = int(keep if keep is not None else SETTINGS.chat_history_limit)
    with _lock:
        try:
            _ensure_db()
            with sqlite3.connect(str(_db_path())) as conn:
                rows = conn.execute(
                    "SELECT ts FROM chat_history WHERE session_id=? ORDER BY ts DESC",
                    (session_id,),
                ).fetchall()
                if len(rows) <= keep:
                    return
                drop = [r[0] for r in rows[keep:]]
                conn.executemany(
                    "DELETE FROM chat_history WHERE session_id=? AND ts=?",
                    [(session_id, ts) for ts in drop],
                )
                conn.commit()
        except Exception as e:
            _log(f"SQLite prune failed ({type(e).__name__}: {e}); using memory")
            items = sorted(_MEM.get(session_id, []), key=lambda x: x["ts"])
            _MEM[session_id] = items[-keep:] if keep > 0 else []


def clear_history(session_id: str) -> None:
    if not session_id:
        return
    with _lock:
        _MEM.pop(session_id, None)
        try:
            _ensure_db()
            with sqlite3.connect(str(_db_path())) as conn:
                conn.execute("DELETE FROM chat_history WHERE session_id=?", (session_id,))
                conn.commit()
        except Exception as e:
            _log(f"SQLite clear failed ({type(e).__name__}: {e})")


def build_context_string(
    history: List[Dict],
    new_message: Optional[str] = None,
    *,
    user_role: str = "user",
    bot_role: str = "bot",
    include_new: bool = True,
) -> str:
    lines: List[str] = []
    for msg in history:
        role = msg.get("role")
        prefix = "Parent:" if role == user_role else "Bot:"
        lines.append(f"{prefix} {msg.get('message') or ''}")
    if include_new and new_message:
        lines.append(f"Parent: {new_message}")
    return "\n".join(lines)
