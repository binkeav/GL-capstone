from __future__ import annotations

import json
import pickle
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


DEFAULT_INDEX_DIRNAME = "invoice_rag"


def index_uploaded_invoice(
    db_path: str | Path,
    processing_result: dict[str, Any],
    *,
    conversation_id: str | None = None,
    index_dir: str | Path | None = None,
) -> str:
    conn = _connect(db_path)
    try:
        _init_rag_tables(conn)
        doc = _document_from_processing_result(processing_result, conversation_id)
        doc_id = _upsert_document(conn, doc)
        _replace_chunks(conn, doc_id, doc["search_text"])
        conn.commit()
    finally:
        conn.close()

    rebuild_rag_index(db_path, index_dir=index_dir)
    return doc_id


def search_uploaded_invoices(
    db_path: str | Path,
    query: str,
    *,
    top_k: int = 5,
    index_dir: str | Path | None = None,
    vendor_name: str | None = None,
    final_state: str | None = None,
) -> list[dict[str, Any]]:
    index_path = _index_dir(db_path, index_dir)
    matrix_path = index_path / "tfidf_matrix.npz"
    vectorizer_path = index_path / "tfidf_vectorizer.pkl"
    chunks_path = index_path / "rag_chunks.json"
    if not matrix_path.exists() or not vectorizer_path.exists() or not chunks_path.exists():
        rebuild_rag_index(db_path, index_dir=index_dir)
    if not matrix_path.exists() or not vectorizer_path.exists() or not chunks_path.exists():
        return []

    matrix = sparse.load_npz(matrix_path)
    with vectorizer_path.open("rb") as f:
        vectorizer = pickle.load(f)
    chunks = json.loads(chunks_path.read_text())
    if matrix.shape[0] == 0 or not chunks:
        return []

    query_vector = vectorizer.transform([_expand_query(query)])
    scores = cosine_similarity(query_vector, matrix).flatten()
    ranked = sorted(
        ((float(score), idx) for idx, score in enumerate(scores) if score > 0),
        reverse=True,
    )

    hits: list[dict[str, Any]] = []
    for score, idx in ranked:
        chunk = dict(chunks[idx])
        if vendor_name and vendor_name.lower() not in str(chunk.get("vendor_name") or "").lower():
            continue
        if final_state and str(chunk.get("final_state") or "").upper() != final_state.upper():
            continue
        chunk["score"] = score
        hits.append(chunk)
        if len(hits) >= top_k:
            break
    return hits


def list_uploaded_invoices(
    db_path: str | Path,
    *,
    limit: int = 50,
    vendor_name: str | None = None,
    final_state: str | None = None,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        _init_rag_tables(conn)
        where = []
        params: list[Any] = []
        if vendor_name:
            where.append("LOWER(vendor_name) LIKE ?")
            params.append(f"%{vendor_name.lower()}%")
        if final_state:
            where.append("UPPER(final_state) = ?")
            params.append(final_state.upper())
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        rows = conn.execute(
            f"""
            SELECT doc_id, source_file, invoice_number, vendor_name, invoice_date,
                   total, currency, final_state, created_at
            FROM invoice_rag_documents
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def rebuild_rag_index(
    db_path: str | Path,
    *,
    index_dir: str | Path | None = None,
) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        _init_rag_tables(conn)
        rows = conn.execute(
            """
            SELECT c.chunk_id, c.doc_id, c.chunk_index, c.chunk_text,
                   d.source_file, d.invoice_number, d.vendor_name, d.total, d.currency,
                   d.final_state, d.created_at
            FROM invoice_rag_chunks c
            JOIN invoice_rag_documents d ON d.doc_id = c.doc_id
            ORDER BY d.created_at, c.chunk_index
            """
        ).fetchall()
    finally:
        conn.close()

    path = _index_dir(db_path, index_dir)
    path.mkdir(parents=True, exist_ok=True)

    chunks = [dict(row) for row in rows]
    texts = [row["chunk_text"] for row in rows]
    if not texts:
        _write_empty_index(path)
        return {"documents_indexed": 0, "chunks_indexed": 0, "index_ready": False}

    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=8000)
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError:
        _write_empty_index(path)
        return {"documents_indexed": 0, "chunks_indexed": 0, "index_ready": False}
    with (path / "tfidf_vectorizer.pkl").open("wb") as f:
        pickle.dump(vectorizer, f)
    sparse.save_npz(path / "tfidf_matrix.npz", matrix)
    (path / "rag_chunks.json").write_text(json.dumps(chunks, indent=2))
    status = {
        "documents_indexed": len({chunk["doc_id"] for chunk in chunks}),
        "chunks_indexed": len(chunks),
        "index_ready": True,
        "embedding_method": "sklearn_tfidf",
    }
    (path / "rag_status.json").write_text(json.dumps(status, indent=2))
    return status


def rag_status(db_path: str | Path, *, index_dir: str | Path | None = None) -> dict[str, Any]:
    path = _index_dir(db_path, index_dir)
    status_path = path / "rag_status.json"
    if status_path.exists():
        return json.loads(status_path.read_text())
    return rebuild_rag_index(db_path, index_dir=index_dir)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_rag_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS invoice_rag_documents (
            doc_id TEXT PRIMARY KEY,
            conversation_id TEXT,
            source_file TEXT,
            invoice_number TEXT,
            vendor_name TEXT,
            invoice_date TEXT,
            total TEXT,
            currency TEXT,
            final_state TEXT,
            payload_json TEXT,
            evidence_json TEXT,
            raw_text TEXT,
            search_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS invoice_rag_chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (doc_id) REFERENCES invoice_rag_documents(doc_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_invoice_rag_doc_invoice_number
            ON invoice_rag_documents(invoice_number);
        CREATE INDEX IF NOT EXISTS idx_invoice_rag_doc_vendor
            ON invoice_rag_documents(vendor_name);
        CREATE INDEX IF NOT EXISTS idx_invoice_rag_chunk_doc
            ON invoice_rag_chunks(doc_id);
        """
    )


def _document_from_processing_result(
    processing_result: dict[str, Any],
    conversation_id: str | None,
) -> dict[str, Any]:
    payload = processing_result.get("invoice_payload") or {}
    evidence = processing_result.get("ocr_evidence") or {}
    validation = processing_result.get("validation_result") or {}
    source_file = evidence.get("source_file") or processing_result.get("source_file")
    invoice_number = payload.get("invoice_number")
    doc_id = _stable_doc_id(source_file, invoice_number, conversation_id)
    search_text = _build_search_text(processing_result)
    now = _utc_now()
    return {
        "doc_id": doc_id,
        "conversation_id": conversation_id,
        "source_file": source_file,
        "invoice_number": invoice_number,
        "vendor_name": payload.get("vendor_name"),
        "invoice_date": payload.get("invoice_date"),
        "total": payload.get("total"),
        "currency": payload.get("currency"),
        "final_state": processing_result.get("final_state") or validation.get("final_state"),
        "payload_json": json.dumps(payload),
        "evidence_json": json.dumps(evidence),
        "raw_text": evidence.get("raw_text") or "",
        "search_text": search_text,
        "created_at": now,
        "updated_at": now,
    }


def _stable_doc_id(source_file: Any, invoice_number: Any, conversation_id: str | None) -> str:
    key = "|".join(str(part or "") for part in (conversation_id, source_file, invoice_number))
    return "ragdoc_" + uuid.uuid5(uuid.NAMESPACE_URL, key).hex


def _upsert_document(conn: sqlite3.Connection, doc: dict[str, Any]) -> str:
    conn.execute(
        """
        INSERT INTO invoice_rag_documents
        (doc_id, conversation_id, source_file, invoice_number, vendor_name, invoice_date,
         total, currency, final_state, payload_json, evidence_json, raw_text, search_text,
         created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(doc_id) DO UPDATE SET
            conversation_id = excluded.conversation_id,
            source_file = excluded.source_file,
            invoice_number = excluded.invoice_number,
            vendor_name = excluded.vendor_name,
            invoice_date = excluded.invoice_date,
            total = excluded.total,
            currency = excluded.currency,
            final_state = excluded.final_state,
            payload_json = excluded.payload_json,
            evidence_json = excluded.evidence_json,
            raw_text = excluded.raw_text,
            search_text = excluded.search_text,
            updated_at = excluded.updated_at
        """,
        (
            doc["doc_id"],
            doc["conversation_id"],
            doc["source_file"],
            doc["invoice_number"],
            doc["vendor_name"],
            doc["invoice_date"],
            doc["total"],
            doc["currency"],
            doc["final_state"],
            doc["payload_json"],
            doc["evidence_json"],
            doc["raw_text"],
            doc["search_text"],
            doc["created_at"],
            doc["updated_at"],
        ),
    )
    return doc["doc_id"]


def _replace_chunks(conn: sqlite3.Connection, doc_id: str, text: str) -> None:
    conn.execute("DELETE FROM invoice_rag_chunks WHERE doc_id = ?", (doc_id,))
    now = _utc_now()
    for idx, chunk in enumerate(_chunk_text(text)):
        conn.execute(
            """
            INSERT INTO invoice_rag_chunks
            (chunk_id, doc_id, chunk_index, chunk_text, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (f"{doc_id}_chunk_{idx}", doc_id, idx, chunk, now),
        )


def _build_search_text(processing_result: dict[str, Any]) -> str:
    payload = processing_result.get("invoice_payload") or {}
    evidence = processing_result.get("ocr_evidence") or {}
    validation = processing_result.get("validation_result") or {}
    rule_results = validation.get("rule_results") or []
    lines = [
        f"Invoice number: {payload.get('invoice_number')}",
        f"Invoice date: {payload.get('invoice_date')}",
        f"PO number: {payload.get('po_number')}",
        f"Vendor: {payload.get('vendor_name')}",
        f"Vendor tax ID: {payload.get('vendor_tax_id')}",
        f"Customer: {payload.get('customer_name')}",
        f"Subtotal: {payload.get('subtotal')}",
        f"Tax: {payload.get('tax')}",
        f"Shipping: {payload.get('shipping')}",
        f"Discounts: {payload.get('discounts')}",
        f"Total: {payload.get('total')} {payload.get('currency') or ''}",
        f"State: {processing_result.get('final_state') or validation.get('final_state')}",
    ]
    if payload.get("order_items"):
        lines.append("Line items:")
        for item in payload.get("order_items", []):
            lines.append(
                " | ".join(
                    str(item.get(key, ""))
                    for key in ("line_no", "description", "qty", "unit_price", "net_amount", "tax_rate", "gross_amount")
                )
            )
    failed = [result for result in rule_results if result.get("status") == "FAIL"]
    if failed:
        lines.append("Failed checks:")
        for result in failed:
            lines.append(
                f"{result.get('rule_id')} {result.get('error_code')} expected {result.get('expected_value')} actual {result.get('actual_value')}"
            )
    if evidence.get("raw_text"):
        lines.append("Raw OCR text:")
        lines.append(evidence["raw_text"])
    return "\n".join(line for line in lines if line and not line.endswith("None"))


def _chunk_text(text: str, chunk_size: int = 1200, overlap: int = 180) -> list[str]:
    clean = (text or "").strip()
    if not clean:
        return []
    chunks = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + chunk_size)
        chunks.append(clean[start:end])
        if end >= len(clean):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _expand_query(query: str) -> str:
    text = query or ""
    lowered = text.lower()
    expansions = []
    if "po" in lowered or "purchase order" in lowered:
        expansions.append("purchase order po_number MATCH_PO_EXISTS")
    if "missing po" in lowered or "missing purchase order" in lowered or "no po" in lowered:
        expansions.append("missing purchase order ERR_MISSING_PO MATCH_PO_EXISTS")
    if "failed" in lowered or "failure" in lowered or "human review" in lowered or "needs review" in lowered:
        expansions.append("FAIL failed checks HUMAN_IN_THE_LOOP")
    if "blocked" in lowered or "critical" in lowered:
        expansions.append("CRITICAL_REJECTION blocked vendor ERR_BLOCKED_VENDOR")
    if "duplicate" in lowered:
        expansions.append("duplicate invoice ERR_CONFIRMED_DUPLICATE ERR_POSSIBLE_DUPLICATE")
    if "gst" in lowered or "gstin" in lowered or "tax id" in lowered:
        expansions.append("GSTIN vendor_tax_id customer_tax_id")
    if "total" in lowered or "amount" in lowered:
        expansions.append("total subtotal tax currency INR")
    if "vendor" in lowered or "supplier" in lowered:
        expansions.append("vendor supplier vendor_name")
    return " ".join([text, *expansions])


def _index_dir(db_path: str | Path, index_dir: str | Path | None) -> Path:
    if index_dir is not None:
        return Path(index_dir)
    return Path(db_path).parent / DEFAULT_INDEX_DIRNAME


def _write_empty_index(path: Path) -> None:
    for file_name in ("tfidf_vectorizer.pkl", "tfidf_matrix.npz", "rag_chunks.json"):
        target = path / file_name
        if target.exists():
            target.unlink()
    status = {
        "documents_indexed": 0,
        "chunks_indexed": 0,
        "index_ready": False,
        "embedding_method": "sklearn_tfidf",
    }
    (path / "rag_chunks.json").write_text("[]")
    (path / "rag_status.json").write_text(json.dumps(status, indent=2))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
