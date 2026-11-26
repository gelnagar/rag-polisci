"""SQLite-backed library: documents, pages, sections, chunks (+FTS5 and vectors), outputs."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

import numpy as np

from .config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    title TEXT, author TEXT, filename TEXT, path TEXT,
    pages INTEGER DEFAULT 0, added_at REAL,
    status TEXT DEFAULT 'pending', note TEXT,
    summary TEXT, digest_mode TEXT, meta TEXT
);
CREATE TABLE IF NOT EXISTS pages (
    doc_id INTEGER, pno INTEGER, label TEXT, text TEXT,
    PRIMARY KEY (doc_id, pno)
);
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY, doc_id INTEGER, idx INTEGER, title TEXT, level INTEGER,
    start_pno INTEGER, end_pno INTEGER, summary TEXT, summary_mode TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY, doc_id INTEGER, idx INTEGER, section_id INTEGER,
    pno_start INTEGER, pno_end INTEGER, text TEXT, embedding BLOB
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='id', tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS outputs (
    id INTEGER PRIMARY KEY, kind TEXT, title TEXT, doc_ids TEXT, content TEXT,
    created_at REAL, meta TEXT
);
CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY, title TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, chat_id INTEGER, role TEXT, content TEXT, meta TEXT,
    created_at REAL
);
"""


class Store:
    def __init__(self, path=DB_PATH) -> None:
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
        self._vec_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    # ------------------------------------------------------------ helpers
    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # ------------------------------------------------------------ documents
    def add_document(self, title: str, author: str, filename: str, path: str) -> int:
        return self.x("INSERT INTO documents(title, author, filename, path, added_at, status) "
                      "VALUES (?,?,?,?,?, 'pending')", (title, author, filename, path, time.time()))

    def update_document(self, doc_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE documents SET {cols} WHERE id=?", (*fields.values(), doc_id))

    def document(self, doc_id: int) -> dict | None:
        r = self.one("SELECT * FROM documents WHERE id=?", (doc_id,))
        return dict(r) if r else None

    def documents(self) -> list[dict]:
        rows = self.q("SELECT d.*, (SELECT COUNT(*) FROM sections s WHERE s.doc_id=d.id) AS n_sections, "
                      "(SELECT COUNT(*) FROM sections s WHERE s.doc_id=d.id AND s.summary IS NOT NULL) "
                      "AS n_summarized, (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS n_chunks "
                      "FROM documents d ORDER BY d.id")
        return [dict(r) for r in rows]

    def delete_document(self, doc_id: int) -> None:
        with self.lock:
            ids = [r[0] for r in self.conn.execute("SELECT id FROM chunks WHERE doc_id=?", (doc_id,))]
            for cid in ids:
                row = self.conn.execute("SELECT text FROM chunks WHERE id=?", (cid,)).fetchone()
                self.conn.execute("INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)",
                                  (cid, row[0]))
            for t in ("chunks", "sections", "pages"):
                self.conn.execute(f"DELETE FROM {t} WHERE doc_id=?", (doc_id,))
            self.conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            self.conn.commit()
        self._vec_cache.pop(doc_id, None)

    # ------------------------------------------------------------ pages / sections
    def add_pages(self, doc_id: int, pages: list[tuple[int, str, str]]) -> None:
        with self.lock:
            self.conn.executemany("INSERT OR REPLACE INTO pages(doc_id, pno, label, text) VALUES (?,?,?,?)",
                                  [(doc_id, p, l, t) for p, l, t in pages])
            self.conn.commit()

    def page_labels(self, doc_id: int) -> dict[int, str]:
        return {r["pno"]: r["label"] for r in self.q("SELECT pno, label FROM pages WHERE doc_id=?", (doc_id,))}

    def pages_text(self, doc_id: int, start_pno: int, end_pno: int) -> list[sqlite3.Row]:
        return self.q("SELECT pno, label, text FROM pages WHERE doc_id=? AND pno BETWEEN ? AND ? ORDER BY pno",
                      (doc_id, start_pno, end_pno))

    def pno_for_label(self, doc_id: int, label: str) -> int | None:
        r = self.one("SELECT pno FROM pages WHERE doc_id=? AND label=? ORDER BY pno LIMIT 1",
                     (doc_id, str(label).strip()))
        return r["pno"] if r else None

    def set_sections(self, doc_id: int, sections: list[dict]) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM sections WHERE doc_id=?", (doc_id,))
            self.conn.executemany(
                "INSERT INTO sections(doc_id, idx, title, level, start_pno, end_pno) VALUES (?,?,?,?,?,?)",
                [(doc_id, i, s["title"], s.get("level", 1), s["start"], s["end"]) for i, s in enumerate(sections)])
            self.conn.commit()

    def sections(self, doc_id: int) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM sections WHERE doc_id=? ORDER BY idx", (doc_id,))]

    def set_section_summary(self, section_id: int, summary: str, mode: str) -> None:
        self.x("UPDATE sections SET summary=?, summary_mode=? WHERE id=?", (summary, mode, section_id))

    # ------------------------------------------------------------ chunks
    def add_chunks(self, doc_id: int, chunks: list[dict], vectors: np.ndarray) -> None:
        with self.lock:
            for c, v in zip(chunks, vectors):
                cur = self.conn.execute(
                    "INSERT INTO chunks(doc_id, idx, section_id, pno_start, pno_end, text, embedding) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (doc_id, c["idx"], c.get("section_id"), c["pno_start"], c["pno_end"], c["text"],
                     np.asarray(v, dtype=np.float32).tobytes()))
                self.conn.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, c["text"]))
            self.conn.commit()
        self._vec_cache.pop(doc_id, None)

    def chunk(self, chunk_id: int) -> dict | None:
        r = self.one("SELECT id, doc_id, idx, section_id, pno_start, pno_end, text FROM chunks WHERE id=?",
                     (chunk_id,))
        return dict(r) if r else None

    def chunks_for_section(self, section_id: int) -> list[dict]:
        return [dict(r) for r in self.q("SELECT id, doc_id, idx, pno_start, pno_end, text FROM chunks "
                                        "WHERE section_id=? ORDER BY idx", (section_id,))]

    def vectors(self, doc_id: int) -> tuple[np.ndarray, np.ndarray]:
        if doc_id not in self._vec_cache:
            rows = self.q("SELECT id, embedding FROM chunks WHERE doc_id=? AND embedding IS NOT NULL "
                          "ORDER BY idx", (doc_id,))
            if rows:
                ids = np.array([r["id"] for r in rows], dtype=np.int64)
                mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
            else:
                ids, mat = np.zeros(0, dtype=np.int64), np.zeros((0, 1), dtype=np.float32)
            self._vec_cache[doc_id] = (ids, mat)
        return self._vec_cache[doc_id]

    def fts(self, match: str, doc_ids: list[int], limit: int) -> list[tuple[int, float]]:
        ph = ",".join("?" * len(doc_ids))
        try:
            rows = self.q(f"SELECT c.id, bm25(chunks_fts) AS s FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
                          f"WHERE chunks_fts MATCH ? AND c.doc_id IN ({ph}) ORDER BY s LIMIT ?",
                          (match, *doc_ids, limit))
        except sqlite3.OperationalError:
            return []
        return [(r["id"], r["s"]) for r in rows]

    # ------------------------------------------------------------ outputs & chats
    def save_output(self, kind: str, title: str, doc_ids: list[int], content: str, meta: dict | None = None) -> int:
        return self.x("INSERT INTO outputs(kind, title, doc_ids, content, created_at, meta) VALUES (?,?,?,?,?,?)",
                      (kind, title, json.dumps(doc_ids), content, time.time(), json.dumps(meta or {})))

    def outputs(self) -> list[dict]:
        return [dict(r) for r in self.q("SELECT id, kind, title, doc_ids, created_at FROM outputs ORDER BY id DESC")]

    def output(self, oid: int) -> dict | None:
        r = self.one("SELECT * FROM outputs WHERE id=?", (oid,))
        return dict(r) if r else None

    def delete_output(self, oid: int) -> None:
        self.x("DELETE FROM outputs WHERE id=?", (oid,))

    def new_chat(self, title: str) -> int:
        return self.x("INSERT INTO chats(title, created_at) VALUES (?, ?)", (title[:80], time.time()))

    def chats(self) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM chats ORDER BY id DESC")]

    def delete_chat(self, chat_id: int) -> None:
        self.x("DELETE FROM messages WHERE chat_id=?", (chat_id,))
        self.x("DELETE FROM chats WHERE id=?", (chat_id,))

    def add_message(self, chat_id: int, role: str, content: str, meta: dict | None = None) -> int:
        return self.x("INSERT INTO messages(chat_id, role, content, meta, created_at) VALUES (?,?,?,?,?)",
                      (chat_id, role, content, json.dumps(meta or {}), time.time()))

    def messages(self, chat_id: int) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM messages WHERE chat_id=? ORDER BY id", (chat_id,))]


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store
