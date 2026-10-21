# Copyright (c) 2026 ln4cy
# This software is released under the MIT License.
# See LICENSE file in the project root for full license details.

"""
Built-in SQLite FTS5 + Knowledge Graph MCP Server for AI Responder.

Provides lightweight, zero-container persistent semantic memory and knowledge graph
storage using Python's standard sqlite3 library with FTS5 (BM25 full-text search).
"""

import datetime
import json
import logging
import os
import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
import config

logger = logging.getLogger(__name__)

# The FastMCP server instance
mcp = FastMCP("Memory Server")

_db_lock = threading.RLock()
_fts_available: Optional[bool] = None


def _get_db_path() -> str:
    """Resolve the active SQLite database path from config."""
    configured = getattr(config, "MEMORY_DB_PATH", "/app/data/memory.db")
    if configured != "/app/data/memory.db" or os.environ.get("MEMORY_DB_PATH"):
        return configured
    history_dir = getattr(config, "HISTORY_DIR", "/app/data/history")
    if history_dir and history_dir != "/app/data/history":
        return os.path.join(history_dir, "memory.db")
    return configured


def _connect() -> sqlite3.Connection:
    """Create a configured SQLite connection and ensure schema exists."""
    global _fts_available
    db_path = _get_db_path()
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path, timeout=15.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kg_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            UNIQUE(subject, predicate, object) ON CONFLICT REPLACE
        );
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_subject ON kg_facts(subject);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_object ON kg_facts(object);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_predicate ON kg_facts(predicate);")

    if _fts_available is not False:
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS kg_facts_fts USING fts5(
                    subject,
                    predicate,
                    object,
                    content='kg_facts',
                    content_rowid='id',
                    tokenize='porter unicode61'
                );
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS kg_facts_ai AFTER INSERT ON kg_facts BEGIN
                    INSERT INTO kg_facts_fts(rowid, subject, predicate, object)
                    VALUES (new.id, new.subject, new.predicate, new.object);
                END;
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS kg_facts_ad AFTER DELETE ON kg_facts BEGIN
                    INSERT INTO kg_facts_fts(kg_facts_fts, rowid, subject, predicate, object)
                    VALUES ('delete', old.id, old.subject, old.predicate, old.object);
                END;
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS kg_facts_au AFTER UPDATE ON kg_facts BEGIN
                    INSERT INTO kg_facts_fts(kg_facts_fts, rowid, subject, predicate, object)
                    VALUES ('delete', old.id, old.subject, old.predicate, old.object);
                    INSERT INTO kg_facts_fts(rowid, subject, predicate, object)
                    VALUES (new.id, new.subject, new.predicate, new.object);
                END;
                """
            )
            _fts_available = True
        except sqlite3.OperationalError as e:
            logger.warning(f"SQLite FTS5 unavailable, falling back to LIKE search: {e}")
            _fts_available = False

    conn.commit()
    return conn


def _build_fts_query(raw_query: str) -> str:
    """Convert a natural-language query into a safe FTS5 OR-prefix query."""
    tokens = re.findall(r"[A-Za-z0-9_!]+", raw_query or "")
    # Strip leading '!' for FTS tokenizer compatibility while keeping hex node IDs
    cleaned = []
    for tok in tokens:
        t = tok.lstrip("!")
        if len(t) >= 2:
            cleaned.append(f'"{t}"*')
    return " OR ".join(cleaned)


@mcp.tool()
def memory_add_fact(subject: str, predicate: str, object: str) -> str:
    """
    Store a persistent fact or relationship in the long-term Knowledge Graph.
    Examples:
      memory_add_fact("!a20a0d78", "has_callsign", "KD9XYZ")
      memory_add_fact("!a20a0d78", "prefers", " metric units")
      memory_add_fact("RoofNode", "has_hardware", "RAK4631 with 20W solar")
    """
    subj = str(subject or "").strip()
    pred = str(predicate or "").strip()
    obj = str(object or "").strip()
    if not subj or not pred or not obj:
        return "Error: subject, predicate, and object are all required."

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _db_lock:
        conn = _connect()
        try:
            # Delete existing identical triple first so FTS triggers stay cleanly in sync
            conn.execute(
                "DELETE FROM kg_facts WHERE subject = ? AND predicate = ? AND object = ?",
                (subj, pred, obj),
            )
            conn.execute(
                "INSERT INTO kg_facts (subject, predicate, object, valid_from) VALUES (?, ?, ?, ?)",
                (subj, pred, obj, now_iso),
            )
            conn.commit()
        finally:
            conn.close()

    return f"Stored fact: ({subj}) --[{pred}]--> ({obj})"


@mcp.tool()
def memory_query_facts(entity: str, direction: str = "both") -> Dict[str, Any]:
    """
    Query the Knowledge Graph for all known facts and relationships involving an entity
    (e.g. a Node ID like '!a20a0d78', a user name, or a topic).
    direction can be 'outgoing' (entity is subject), 'incoming' (entity is object), or 'both'.
    """
    target = str(entity or "").strip()
    if not target:
        return {"entity": "", "facts": []}

    dir_norm = (direction or "both").lower().strip()
    with _db_lock:
        conn = _connect()
        try:
            if dir_norm == "outgoing":
                cursor = conn.execute(
                    "SELECT subject, predicate, object, valid_from FROM kg_facts "
                    "WHERE subject = ? ORDER BY valid_from ASC, id ASC",
                    (target,),
                )
            elif dir_norm == "incoming":
                cursor = conn.execute(
                    "SELECT subject, predicate, object, valid_from FROM kg_facts "
                    "WHERE object = ? ORDER BY valid_from ASC, id ASC",
                    (target,),
                )
            else:
                cursor = conn.execute(
                    "SELECT subject, predicate, object, valid_from FROM kg_facts "
                    "WHERE subject = ? OR object = ? ORDER BY valid_from ASC, id ASC",
                    (target, target),
                )
            facts = [
                {
                    "subject": row["subject"],
                    "predicate": row["predicate"],
                    "object": row["object"],
                    "valid_from": row["valid_from"],
                }
                for row in cursor.fetchall()
            ]
        finally:
            conn.close()

    return {"entity": target, "direction": dir_norm, "facts": facts}


@mcp.tool()
def memory_search(query: str, limit: int = 10) -> str:
    """
    Search long-term memory (stored facts, user preferences, node details, and archived
    conversations) using BM25 full-text search. Call this FIRST when recalling past
    events, user details, or historical discussions.
    """
    q = str(query or "").strip()
    if not q:
        return "No search query provided."

    max_rows = max(1, min(int(limit or 10), 50))
    rows: List[sqlite3.Row] = []

    with _db_lock:
        conn = _connect()
        try:
            fts_query = _build_fts_query(q)
            if _fts_available and fts_query:
                try:
                    cursor = conn.execute(
                        """
                        SELECT f.subject, f.predicate, f.object, f.valid_from
                        FROM kg_facts_fts fts
                        JOIN kg_facts f ON f.id = fts.rowid
                        WHERE kg_facts_fts MATCH ?
                          AND f.predicate NOT IN ('is_a', 'participated_in')
                        ORDER BY bm25(kg_facts_fts) ASC, f.valid_from DESC
                        LIMIT ?
                        """,
                        (fts_query, max_rows),
                    )
                    rows = cursor.fetchall()
                except sqlite3.OperationalError as e:
                    logger.debug(f"FTS5 query fallback for '{q}': {e}")

            # Fallback to substring LIKE search if FTS returned nothing
            if not rows:
                like_pat = f"%{q}%"
                cursor = conn.execute(
                    """
                    SELECT subject, predicate, object, valid_from
                    FROM kg_facts
                    WHERE (subject LIKE ? OR predicate LIKE ? OR object LIKE ?)
                      AND predicate NOT IN ('is_a', 'participated_in')
                    ORDER BY valid_from DESC, id DESC
                    LIMIT ?
                    """,
                    (like_pat, like_pat, like_pat, max_rows),
                )
                rows = cursor.fetchall()
        finally:
            conn.close()

    if not rows:
        return f"No matching memories found for '{q}'."

    lines = [f"Memory search results for '{q}' ({len(rows)} found):"]
    for r in rows:
        lines.append(f"- [{r['subject']}] {r['predicate']}: {r['object']} ({r['valid_from']})")
    return "\n".join(lines)


@mcp.tool()
def memory_status() -> Dict[str, Any]:
    """Internal tool: Return database statistics (entities, triples, disk size)."""
    db_path = _get_db_path()
    with _db_lock:
        conn = _connect()
        try:
            total_triples = conn.execute("SELECT COUNT(*) FROM kg_facts").fetchone()[0]
            total_entities = conn.execute(
                """
                SELECT COUNT(DISTINCT entity) FROM (
                    SELECT subject AS entity FROM kg_facts
                    UNION
                    SELECT object AS entity FROM kg_facts
                )
                """
            ).fetchone()[0]
        finally:
            conn.close()

    size_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    return {
        "total_triples": total_triples,
        "total_entities": total_entities,
        "db_size_bytes": size_bytes,
    }


@mcp.tool()
def memory_delete_hub(target: str) -> Dict[str, Any]:
    """Internal tool: Prune all recorded turns and links for a hub or topic."""
    hub = str(target or "").strip()
    if not hub:
        return {"deleted": 0}

    with _db_lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "DELETE FROM kg_facts WHERE subject = ? OR object = ?",
                (hub, hub),
            )
            deleted = cur.rowcount
            conn.commit()
        finally:
            conn.close()

    return {"target": hub, "deleted": deleted}
