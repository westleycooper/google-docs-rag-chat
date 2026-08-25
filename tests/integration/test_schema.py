"""The parts of ADR-0002 and ADR-0004 that only a real database can prove."""

from __future__ import annotations

import uuid

import pytest

SOURCE_SQL = """
INSERT INTO sources (id, name, provider, auth_mode, credential_ref, principal)
VALUES (%s, %s, 'google_drive', 'service_account', 'kms://ref', 'ingest@example.com')
"""
DOC_SQL = """
INSERT INTO documents (id, source_id, external_id, title, mime_type)
VALUES (%s, %s, %s, %s, 'application/vnd.google-apps.document')
"""
CHUNK_SQL = """
INSERT INTO chunks (id, document_id, ordinal, text, token_count, heading_path,
                    embedding, embedding_model)
VALUES (%s, %s, %s, %s, %s, %s, %s, 'voyage-3-large')
"""


@pytest.fixture
def corpus(conn, vector_literal):
    """A source, a document and three chunks. Rolled back after each test."""
    source_id, doc_id = uuid.uuid4(), uuid.uuid4()
    cur = conn.cursor()
    cur.execute(SOURCE_SQL, (source_id, f"Drive {source_id}"))
    cur.execute(DOC_SQL, (doc_id, source_id, "1AbC", "Q3 Revenue Review"))
    chunks = [
        (
            uuid.uuid4(),
            0,
            "Revenue for the quarter rose twelve percent against plan.",
            9,
            ["Finance", "Summary"],
            3,
        ),
        (
            uuid.uuid4(),
            1,
            "Project PRJ-4471 was descoped after the vendor review.",
            8,
            ["Delivery"],
            11,
        ),
        (
            uuid.uuid4(),
            2,
            "Headcount grew by four in the delivery organisation.",
            8,
            ["People"],
            29,
        ),
    ]
    for cid, ordinal, text, tokens, headings, seed in chunks:
        cur.execute(CHUNK_SQL, (cid, doc_id, ordinal, text, tokens, headings, vector_literal(seed)))
    return {"source_id": source_id, "document_id": doc_id, "chunk_ids": [c[0] for c in chunks]}


# -- the trigger ----------------------------------------------------------


def test_trigger_populates_search_vector_without_the_application(conn, corpus):
    """Lexical recall that depends on remembering to set a column rots silently."""
    cur = conn.cursor()
    cur.execute(
        "SELECT search_vector IS NOT NULL FROM chunks WHERE document_id = %s",
        (corpus["document_id"],),
    )
    assert all(row[0] for row in cur.fetchall())


def test_heading_terms_carry_more_weight_than_body_terms(conn, corpus):
    cur = conn.cursor()
    cur.execute(
        "SELECT search_vector::text FROM chunks WHERE document_id = %s AND ordinal = 0",
        (corpus["document_id"],),
    )
    tsvector = cur.fetchone()[0]
    assert "'financ':1A" in tsvector  # from heading_path
    assert "'revenu':3B" in tsvector  # from body text


def test_the_trigger_fires_on_update_too(conn, corpus):
    cur = conn.cursor()
    cur.execute(
        "UPDATE chunks SET text = %s WHERE id = %s",
        ("Entirely different subject matter about logistics.", corpus["chunk_ids"][0]),
    )
    cur.execute(
        "SELECT search_vector @@ to_tsquery('english', 'logistics') FROM chunks WHERE id = %s",
        (corpus["chunk_ids"][0],),
    )
    assert cur.fetchone()[0] is True


# -- ADR-0004: the two halves of recall -----------------------------------


def test_exact_identifier_lookup_succeeds_lexically(conn, corpus):
    """The motivating case: dense search smears IDs, BM25 does not."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ordinal FROM chunks, to_tsquery('english', 'PRJ-4471') q
        WHERE document_id = %s AND search_vector @@ q
        ORDER BY ts_rank_cd(search_vector, q) DESC
        """,
        (corpus["document_id"],),
    )
    assert [r[0] for r in cur.fetchall()] == [1]


def test_dense_search_ranks_by_cosine_distance(conn, corpus, vector_literal):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ordinal, (embedding <=> %s::vector) AS distance
        FROM chunks WHERE document_id = %s ORDER BY distance LIMIT 3
        """,
        (vector_literal(3), corpus["document_id"]),
    )
    rows = cur.fetchall()
    assert rows[0][0] == 0  # the chunk embedded with seed 3
    assert rows[0][1] == pytest.approx(0.0, abs=1e-9)
    assert [r[1] for r in rows] == sorted(r[1] for r in rows)


def test_the_hnsw_index_exists_and_is_cosine(conn):
    cur = conn.cursor()
    cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_chunks_embedding_hnsw'")
    definition = cur.fetchone()[0]
    assert "USING hnsw" in definition
    assert "vector_cosine_ops" in definition


def test_the_embedding_column_matches_the_configured_width(conn):
    """ADR-0002: the boot-time check compares the provider against this."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT format_type(atttypid, atttypmod) FROM pg_attribute
        WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'
        """
    )
    assert cur.fetchone()[0] == "vector(1024)"


# -- integrity ------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "params", "constraint"),
    [
        (CHUNK_SQL, (-1, "x", 1), "ordinal_non_negative"),
        (CHUNK_SQL, (98, "x", 0), "token_count_positive"),
    ],
)
def test_chunk_check_constraints_reject_bad_rows(conn, corpus, sql, params, constraint):
    psycopg = pytest.importorskip("psycopg")
    ordinal, text, tokens = params
    cur = conn.cursor()
    with pytest.raises(psycopg.errors.CheckViolation, match=constraint):
        cur.execute(sql, (uuid.uuid4(), corpus["document_id"], ordinal, text, tokens, [], None))


def test_a_terminal_run_must_record_when_it_finished(conn, corpus):
    psycopg = pytest.importorskip("psycopg")
    cur = conn.cursor()
    with pytest.raises(psycopg.errors.CheckViolation, match="terminal_runs_have_finished_at"):
        cur.execute(
            "INSERT INTO ingestion_runs (id, source_id, state) VALUES (%s, %s, 'completed')",
            (uuid.uuid4(), corpus["source_id"]),
        )


def test_a_failed_run_must_record_why(conn, corpus):
    psycopg = pytest.importorskip("psycopg")
    cur = conn.cursor()
    with pytest.raises(psycopg.errors.CheckViolation, match="failed_runs_record_why"):
        cur.execute(
            "INSERT INTO ingestion_runs (id, source_id, state, finished_at) "
            "VALUES (%s, %s, 'failed', now())",
            (uuid.uuid4(), corpus["source_id"]),
        )


def test_a_skip_reason_outside_the_vocabulary_is_rejected(conn, corpus):
    psycopg = pytest.importorskip("psycopg")
    run_id = uuid.uuid4()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ingestion_runs (id, source_id, state) VALUES (%s, %s, 'running')",
        (run_id, corpus["source_id"]),
    )
    with pytest.raises(psycopg.errors.CheckViolation, match="reason_is_known"):
        cur.execute(
            "INSERT INTO skip_records (run_id, external_id, reason, principal, occurred_at) "
            "VALUES (%s, 'f', 'because-i-said-so', 'p', now())",
            (run_id,),
        )


def test_the_same_file_in_two_sources_is_two_documents(conn, corpus):
    """Collapsing them would let one source's grant read into another's corpus."""
    other_source = uuid.uuid4()
    cur = conn.cursor()
    cur.execute(SOURCE_SQL, (other_source, f"Other {other_source}"))
    cur.execute(DOC_SQL, (uuid.uuid4(), other_source, "1AbC", "Same File"))
    cur.execute("SELECT count(*) FROM documents WHERE external_id = '1AbC'")
    assert cur.fetchone()[0] == 2


def test_the_same_external_id_twice_in_one_source_is_rejected(conn, corpus):
    psycopg = pytest.importorskip("psycopg")
    cur = conn.cursor()
    with pytest.raises(psycopg.errors.UniqueViolation):
        cur.execute(DOC_SQL, (uuid.uuid4(), corpus["source_id"], "1AbC", "Duplicate"))


def test_deleting_a_document_cascades_to_its_chunks(conn, corpus):
    """A shrunk re-ingest must not leave orphans that remain citable."""
    cur = conn.cursor()
    cur.execute("DELETE FROM documents WHERE id = %s", (corpus["document_id"],))
    cur.execute("SELECT count(*) FROM chunks WHERE document_id = %s", (corpus["document_id"],))
    assert cur.fetchone()[0] == 0


def test_deleting_a_source_cascades_all_the_way_down(conn, corpus):
    cur = conn.cursor()
    cur.execute("DELETE FROM sources WHERE id = %s", (corpus["source_id"],))
    cur.execute("SELECT count(*) FROM documents WHERE source_id = %s", (corpus["source_id"],))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM chunks WHERE document_id = %s", (corpus["document_id"],))
    assert cur.fetchone()[0] == 0
