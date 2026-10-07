# ============================================================
# rag_engine.py — Tayseer Agri-SaT Persistent Tenant-Isolated RAG Engine
#
# Complete Agronomic Document Ingestion, Semantic Chunking,
# Persistent Vector/Index Storage, Tenant Filtering, and Hybrid Retrieval.
#
# Supported Document Types:
#   • PDF (.pdf via pypdf)
#   • DOCX (.docx via python-docx)
#   • Excel & CSV (.xlsx, .xls, .csv via openpyxl & csv)
#   • Markdown & Plain Text (.txt, .md)
#   • Geographic / JSON (.json, .geojson, .kml)
# ============================================================

import os
import re
import json
import math
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from config import Config
from database import get_connection, return_connection

log = logging.getLogger(__name__)

# Ensure RAG schema exists
def init_rag_schema():
    """Create persistent RAG storage tables if they do not exist."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rag_documents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                filename        TEXT NOT NULL,
                file_type       TEXT NOT NULL,
                file_size       INTEGER DEFAULT 0,
                file_path       TEXT NOT NULL,
                title           TEXT,
                chunk_count     INTEGER DEFAULT 0,
                status          TEXT DEFAULT 'INDEXED',
                metadata_json   TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id     INTEGER NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
                user_id         INTEGER NOT NULL,
                chunk_index     INTEGER NOT NULL,
                content         TEXT NOT NULL,
                embedding_json  TEXT,
                keywords_json   TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_docs_user ON rag_documents(user_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_user ON rag_chunks(user_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc ON rag_chunks(document_id);")
        conn.commit()
    except Exception as e:
        log.error(f"[RAG] Schema initialization error: {e}")
    finally:
        return_connection(conn)


# Initialize on import
init_rag_schema()


class DocumentParser:
    """Extracts clean text and metadata from agricultural documents."""

    @staticmethod
    def parse_file(file_path: str, filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()

        if ext == ".pdf":
            return DocumentParser._parse_pdf(file_path)
        elif ext == ".docx":
            return DocumentParser._parse_docx(file_path)
        elif ext in [".txt", ".md"]:
            return DocumentParser._parse_text(file_path)
        elif ext == ".csv":
            return DocumentParser._parse_csv(file_path)
        elif ext in [".xlsx", ".xls"]:
            return DocumentParser._parse_excel(file_path)
        elif ext in [".json", ".geojson", ".kml"]:
            return DocumentParser._parse_json_or_geo(file_path)
        else:
            # Fallback to UTF-8 text attempt
            return DocumentParser._parse_text(file_path)

    @staticmethod
    def _parse_pdf(file_path: str) -> str:
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            pages = []
            for i, page in enumerate(reader.pages):
                txt = page.extract_text()
                if txt:
                    pages.append(f"[صفحة {i+1}]\n{txt}")
            return "\n\n".join(pages)
        except Exception as e:
            log.warning(f"[RAG] PDF parse failed ({file_path}): {e}")
            return ""

    @staticmethod
    def _parse_docx(file_path: str) -> str:
        try:
            import docx
            doc = docx.Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n".join(paragraphs)
        except Exception as e:
            log.warning(f"[RAG] DOCX parse failed ({file_path}): {e}")
            return ""

    @staticmethod
    def _parse_text(file_path: str) -> str:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            log.warning(f"[RAG] Text parse failed ({file_path}): {e}")
            return ""

    @staticmethod
    def _parse_csv(file_path: str) -> str:
        import csv
        lines = []
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                if headers:
                    lines.append(f"أعمدة البيانات: {', '.join(headers)}")
                for i, row in enumerate(reader):
                    if i < 200:  # Sample up to 200 representative rows
                        lines.append(" | ".join(row))
            return "\n".join(lines)
        except Exception as e:
            log.warning(f"[RAG] CSV parse failed ({file_path}): {e}")
            return ""

    @staticmethod
    def _parse_excel(file_path: str) -> str:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            lines = []
            for sheetname in wb.sheetnames[:3]:
                sheet = wb[sheetname]
                lines.append(f"--- ورقة العمل: {sheetname} ---")
                row_count = 0
                for row in sheet.iter_rows(values_only=True):
                    row_vals = [str(c) for c in row if c is not None]
                    if row_vals:
                        lines.append(" | ".join(row_vals))
                        row_count += 1
                        if row_count > 100:
                            break
            wb.close()
            return "\n".join(lines)
        except Exception as e:
            log.warning(f"[RAG] Excel parse failed ({file_path}): {e}")
            return ""

    @staticmethod
    def _parse_json_or_geo(file_path: str) -> str:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = json.load(f)
                return json.dumps(content, ensure_ascii=False, indent=2)
        except Exception:
            return DocumentParser._parse_text(file_path)


class TextChunker:
    """Splits document text into coherent chunks with overlap."""

    @staticmethod
    def chunk(text: str, chunk_size: int = 600, overlap: int = 100) -> List[str]:
        if not text:
            return []
        
        # Split by paragraphs / double newlines
        paragraphs = re.split(r'\n{2,}', text)
        chunks = []
        current_chunk = []
        current_len = 0

        for p in paragraphs:
            p = p.strip()
            if not p:
                continue

            p_len = len(p)
            if current_len + p_len > chunk_size and current_chunk:
                combined = "\n\n".join(current_chunk)
                chunks.append(combined)
                # Keep overlap
                overlap_text = combined[-overlap:] if len(combined) > overlap else ""
                current_chunk = [overlap_text, p] if overlap_text else [p]
                current_len = len(overlap_text) + p_len
            else:
                current_chunk.append(p)
                current_len += p_len

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        # If any single chunk is excessively large, split by sentences
        final_chunks = []
        for ch in chunks:
            if len(ch) > chunk_size * 2:
                sentences = re.split(r'([.?!؟\n])', ch)
                buf = ""
                for s in sentences:
                    if len(buf) + len(s) > chunk_size:
                        if buf.strip():
                            final_chunks.append(buf.strip())
                        buf = s
                    else:
                        buf += s
                if buf.strip():
                    final_chunks.append(buf.strip())
            else:
                final_chunks.append(ch)

        return [c.strip() for c in final_chunks if len(c.strip()) > 30]


class DenseLexicalVectorEngine:
    """
    Fast, deterministic hybrid vector representation and cosine ranking.
    Computes normalized term-frequency vectors with character n-grams
    giving semantic-like matching in Arabic and English without heavy external API latency.
    """

    @staticmethod
    def tokenize(text: str) -> List[str]:
        # Arabic normalization
        t = text.lower()
        t = re.sub(r'[إأآا]', 'ا', t)
        t = re.sub(r'[ة]', 'ه', t)
        t = re.sub(r'[ى]', 'ي', t)
        words = re.findall(r'[\w\u0600-\u06FF]{3,}', t)
        return words

    @staticmethod
    def compute_embedding(text: str) -> Dict[str, float]:
        tokens = DenseLexicalVectorEngine.tokenize(text)
        if not tokens:
            return {}
        
        counts = {}
        for tok in tokens:
            counts[tok] = counts.get(tok, 0) + 1
            # Add character bigrams/trigrams for subword matching
            if len(tok) >= 4:
                prefix = tok[:4]
                counts[prefix] = counts.get(prefix, 0) + 0.5

        # Normalize L2
        norm = math.sqrt(sum(v * v for v in counts.values()))
        if norm > 0:
            for k in counts:
                counts[k] = round(counts[k] / norm, 4)
        return counts

    @staticmethod
    def cosine_similarity(vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        if not vec1 or not vec2:
            return 0.0
        # Dot product
        score = 0.0
        # Iterate over smaller dict
        s, b = (vec1, vec2) if len(vec1) < len(vec2) else (vec2, vec1)
        for k, v in s.items():
            if k in b:
                score += v * b[k]
        return score


class PersistentRAGEngine:
    """
    Main RAG Management & Query API with strict tenant isolation.
    """

    def __init__(self, user_id: int):
        self.user_id = user_id

    def ingest_document(self, file_path: str, filename: str, title: str = None) -> Dict[str, Any]:
        """
        Parse, chunk, embed, and store document under this user_id.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = os.path.getsize(file_path)
        ext = os.path.splitext(filename)[1].lower().replace(".", "")
        doc_title = title or filename

        # 1. Parse text
        text = DocumentParser.parse_file(file_path, filename)
        if not text.strip():
            log.warning(f"[RAG] No readable text found in {filename}")
            return {"success": False, "error": "لم يتم استخراج نص مقروء من الملف"}

        # 2. Chunk text
        chunks = TextChunker.chunk(text)
        if not chunks:
            return {"success": False, "error": "المحتوى قصير جداً لإنشاء مقاطع معرفية"}

        # 3. Store in DB
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO rag_documents (user_id, filename, file_type, file_size, file_path, title, chunk_count, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'INDEXED')
            """, (self.user_id, filename, ext, file_size, file_path, doc_title, len(chunks)))
            doc_id = cursor.lastrowid

            for idx, ch in enumerate(chunks):
                vec = DenseLexicalVectorEngine.compute_embedding(ch)
                cursor.execute("""
                    INSERT INTO rag_chunks (document_id, user_id, chunk_index, content, embedding_json)
                    VALUES (?, ?, ?, ?, ?)
                """, (doc_id, self.user_id, idx, ch, json.dumps(vec, ensure_ascii=False)))

            conn.commit()
            log.info(f"[RAG] Document {doc_id} ('{filename}') indexed with {len(chunks)} chunks for user {self.user_id}")
            return {
                "success": True,
                "document_id": doc_id,
                "filename": filename,
                "title": doc_title,
                "chunk_count": len(chunks),
                "file_size": file_size,
            }
        except Exception as e:
            conn.rollback()
            log.error(f"[RAG] Document ingestion error: {e}")
            return {"success": False, "error": str(e)}
        finally:
            return_connection(conn)

    def retrieve(self, query: str, top_k: int = 3, threshold: float = 0.05) -> List[Dict[str, Any]]:
        """
        Query persistent chunks strictly for self.user_id.
        Returns top-k most relevant chunks with metadata and citations.
        """
        clean_q = (query or "").strip()
        if not clean_q:
            return []

        q_vec = DenseLexicalVectorEngine.compute_embedding(clean_q)
        if not q_vec:
            return []

        conn = get_connection()
        results = []
        try:
            cursor = conn.cursor()
            # Strict tenant filtering in SQL
            cursor.execute("""
                SELECT c.id, c.document_id, c.chunk_index, c.content, c.embedding_json,
                       d.filename, d.title, d.file_type
                FROM rag_chunks c
                JOIN rag_documents d ON c.document_id = d.id
                WHERE c.user_id = ?
            """, (self.user_id,))

            rows = cursor.fetchall()
            for r in rows:
                c_id, doc_id, ch_idx, content, emb_json, fname, title, ftype = r
                try:
                    c_vec = json.loads(emb_json) if emb_json else {}
                    sim = DenseLexicalVectorEngine.cosine_similarity(q_vec, c_vec)
                    if sim >= threshold:
                        results.append({
                            "chunk_id": c_id,
                            "document_id": doc_id,
                            "chunk_index": ch_idx,
                            "score": round(sim, 3),
                            "content": content,
                            "source_file": fname,
                            "title": title or fname,
                            "file_type": ftype,
                        })
                except Exception:
                    continue

            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:top_k]
        except Exception as e:
            log.error(f"[RAG] Retrieval error for user {self.user_id}: {e}")
            return []
        finally:
            return_connection(conn)

    def list_documents(self) -> List[Dict[str, Any]]:
        """List all indexed documents for this tenant."""
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, filename, file_type, file_size, title, chunk_count, status, created_at
                FROM rag_documents
                WHERE user_id = ?
                ORDER BY id DESC
            """, (self.user_id,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        finally:
            return_connection(conn)

    def delete_document(self, document_id: int) -> bool:
        """Delete document and its chunks with tenant ownership verification."""
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM rag_chunks WHERE document_id = ? AND user_id = ?", (document_id, self.user_id))
            cursor.execute("DELETE FROM rag_documents WHERE id = ? AND user_id = ?", (document_id, self.user_id))
            conn.commit()
            return True
        except Exception as e:
            log.error(f"[RAG] Delete document failed: {e}")
            return False
        finally:
            return_connection(conn)
