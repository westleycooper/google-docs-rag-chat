"""pgvector-backed `VectorStore` (ADR-0004, ADR-0011).

Raw SQL through SQLAlchemy Core rather than the ORM. Both search paths are
Postgres-specific in ways an abstraction would only obscure -- the `<=>` cosine
operator, `plainto_tsquery`, `ts_rank_cd` -- and writing them plainly is the
honest option.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from ragoogle_core.retrieval.chunk import Chunk, DocumentRef
from ragoogle_core.retrieval.embedding import EmbeddingSpec, EmbeddingVector
from ragoogle_core.retrieval.ranking import Candidate, RetrievalMethod
from ragoogle_core.shared.identifiers import ChunkId, DocumentId, SourceId

# Every read projects the same columns, so one row mapper serves all of them.
_CHUNK_COLUMNS = """
    c.id            AS chunk_id,
    c.ordinal       AS ordinal,
    c.text          AS text,
    c.token_count   AS token_count,
    c.heading_path  AS heading_path,
    c.metadata_json AS metadata_json,
    d.id            AS document_id,
    d.source_id     AS source_id,
    d.external_id   AS external_id,
    d.title         AS title,
    d.mime_type     AS mime_type,
    d.web_url       AS web_url,
    d.modified_at   AS modified_at
"""


def _to_chunk(row: object) -> Chunk:
    r = row._mapping  # type: ignore[attr-defined]
    return Chunk(
        chunk_id=ChunkId(r["chunk_id"]),
        document=DocumentRef(
            document_id=DocumentId(r["document_id"]),
            source_id=SourceId(r["source_id"]),
            external_id=r["external_id"],
            title=r["title"],
            mime_type=r["mime_type"],
            web_url=r["web_url"],
            modified_at=r["modified_at"],
        ),
        ordinal=r["ordinal"],
        text=r["text"],
        token_count=r["token_count"],
        heading_path=tuple(r["heading_path"] or ()),
        metadata=dict(r["metadata_json"] or {}),
    )


def _vector_literal(vector: EmbeddingVector) -> str:
    """pgvector's text input format."""
    return "[" + ",".join(repr(float(v)) for v in vector.values) + "]"


class PgVectorStore:
    """Implements `ragoogle_core.ports.VectorStore`."""

    def __init__(self, engine: AsyncEngine, spec: EmbeddingSpec) -> None:
        self._engine = engine
        self._spec = spec

    @property
    def spec(self) -> EmbeddingSpec:
        return self._spec

    async def verify_schema(self) -> None:
        """Compare the deployed column against the configured provider.

        ADR-0002 makes this a fail-at-boot condition. Called from the composition
        root before the app serves traffic, because the alternative -- discovering
        the mismatch on the first query -- means either an error in front of a
        user or, worse, silently truncated vectors.
        """
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                    "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
                )
            )
            deployed = result.scalar_one_or_none()
        if deployed is None:
            raise RuntimeError("chunks.embedding does not exist; run alembic upgrade head")
        width = int(str(deployed).removeprefix("vector(").removesuffix(")"))
        self._spec.require_compatible(EmbeddingSpec(self._spec.model, width))

    # -- writes -----------------------------------------------------------

    async def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[EmbeddingVector]) -> None:
        if not chunks:
            return
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")

        # search_vector is deliberately absent from this statement: the trigger
        # owns it, so every writer gets correct lexical indexing (ADR-0011).
        statement = text(
            """
            INSERT INTO chunks (id, document_id, ordinal, text, token_count,
                                heading_path, metadata_json, embedding, embedding_model)
            VALUES (:id, :document_id, :ordinal, :text, :token_count,
                    :heading_path, CAST(:metadata_json AS jsonb),
                    CAST(:embedding AS vector), :embedding_model)
            ON CONFLICT (document_id, ordinal) DO UPDATE SET
                id              = EXCLUDED.id,
                text            = EXCLUDED.text,
                token_count     = EXCLUDED.token_count,
                heading_path    = EXCLUDED.heading_path,
                metadata_json   = EXCLUDED.metadata_json,
                embedding       = EXCLUDED.embedding,
                embedding_model = EXCLUDED.embedding_model
            """
        )
        import json

        rows = [
            {
                "id": chunk.chunk_id.value,
                "document_id": chunk.document.document_id.value,
                "ordinal": chunk.ordinal,
                "text": chunk.text,
                "token_count": chunk.token_count,
                "heading_path": list(chunk.heading_path),
                "metadata_json": json.dumps(chunk.metadata),
                "embedding": _vector_literal(vector),
                "embedding_model": vector.spec.model,
            }
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        async with self._engine.begin() as conn:
            await conn.execute(statement, rows)

    async def delete_document(self, document_id: DocumentId) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("DELETE FROM chunks WHERE document_id = :document_id"),
                {"document_id": document_id.value},
            )
        return result.rowcount or 0

    # -- reads ------------------------------------------------------------

    async def dense_search(
        self,
        query: EmbeddingVector,
        *,
        limit: int,
        sources: Sequence[SourceId] | None = None,
    ) -> list[Candidate]:
        self._spec.require_compatible(query.spec)
        source_filter = "AND d.source_id = ANY(:sources)" if sources else ""
        statement = text(
            f"""
            SELECT c.id AS chunk_id, (c.embedding <=> CAST(:query AS vector)) AS distance
            FROM chunks c JOIN documents d ON d.id = c.document_id
            WHERE c.embedding IS NOT NULL {source_filter}
            ORDER BY c.embedding <=> CAST(:query AS vector)
            LIMIT :limit
            """
        )
        params: dict[str, object] = {"query": _vector_literal(query), "limit": limit}
        if sources:
            params["sources"] = [s.value for s in sources]

        async with self._engine.connect() as conn:
            result = await conn.execute(statement, params)
            # Cosine *distance* in [0, 2]; report similarity so a larger score
            # means a better match, consistent with every other Candidate.
            return [
                Candidate(ChunkId(row.chunk_id), 1.0 - float(row.distance), RetrievalMethod.DENSE)
                for row in result
            ]

    async def lexical_search(
        self,
        query: str,
        *,
        limit: int,
        sources: Sequence[SourceId] | None = None,
    ) -> list[Candidate]:
        """Rank by `ts_rank_cd` -- see ADR-0012 for why this is not literally BM25.

        `plainto_tsquery` rather than `to_tsquery`: user input reaches this
        directly, and `to_tsquery` raises a syntax error on ordinary punctuation.
        A chat box that 500s on an apostrophe is not a search feature.
        """
        source_filter = "AND d.source_id = ANY(:sources)" if sources else ""
        statement = text(
            f"""
            SELECT c.id AS chunk_id,
                   ts_rank_cd(c.search_vector, q, 32) AS rank
            FROM chunks c
            JOIN documents d ON d.id = c.document_id,
                 plainto_tsquery('english', :query) q
            WHERE c.search_vector @@ q {source_filter}
            ORDER BY rank DESC, c.id
            LIMIT :limit
            """
        )
        params: dict[str, object] = {"query": query, "limit": limit}
        if sources:
            params["sources"] = [s.value for s in sources]

        async with self._engine.connect() as conn:
            result = await conn.execute(statement, params)
            return [
                Candidate(ChunkId(row.chunk_id), float(row.rank), RetrievalMethod.LEXICAL)
                for row in result
            ]

    async def fetch(self, chunk_ids: Sequence[ChunkId]) -> list[Chunk]:
        """Hydrate by id, preserving the caller's ordering.

        Preserving order matters: the caller's sequence *is* the ranking, and
        returning rows in whatever order Postgres chose would silently discard it.
        """
        if not chunk_ids:
            return []
        statement = text(
            f"""
                SELECT {_CHUNK_COLUMNS}
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.id IN :ids
                """
        ).bindparams(bindparam("ids", expanding=True))
        async with self._engine.connect() as conn:
            result = await conn.execute(statement, {"ids": [c.value for c in chunk_ids]})
            by_id: dict[uuid.UUID, Chunk] = {}
            for row in result:
                chunk = _to_chunk(row)
                by_id[chunk.chunk_id.value] = chunk
        return [by_id[c.value] for c in chunk_ids if c.value in by_id]
