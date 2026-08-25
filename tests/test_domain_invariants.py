"""Constructor-enforced invariants across the domain.

Every one of these is a bug the domain refuses to represent, rather than one it
detects later. That distinction is the reason the domain has no I/O in it.
"""

import uuid

import pytest

from ragoogle_core.retrieval import Chunk, Citation, DocumentRef, EmbeddingSpec, EmbeddingVector
from ragoogle_core.retrieval.ranking import RetrievalMethod
from ragoogle_core.shared.errors import ConfigurationError, InvariantViolation
from ragoogle_core.shared.identifiers import ChunkId, DocumentId, SourceId

VOYAGE = EmbeddingSpec(model="voyage-3-large", dimensions=4)


def doc(**kw):
    defaults = dict(
        document_id=DocumentId.new(),
        source_id=SourceId.new(),
        external_id="1AbC",
        title="Q3 Revenue",
        mime_type="application/vnd.google-apps.document",
    )
    return DocumentRef(**{**defaults, **kw})


def chunk(**kw):
    defaults = dict(
        chunk_id=ChunkId.new(), document=doc(), ordinal=0, text="revenue rose", token_count=3
    )
    return Chunk(**{**defaults, **kw})


# -- identifiers ----------------------------------------------------------


def test_identifiers_of_different_kinds_are_not_interchangeable():
    raw = uuid.uuid4()
    assert DocumentId(raw) != ChunkId(raw)


def test_identifiers_round_trip_through_strings():
    original = SourceId.new()
    assert SourceId.parse(str(original)) == original


# -- DocumentRef / Chunk --------------------------------------------------


def test_blank_title_is_rejected():
    with pytest.raises(InvariantViolation, match="title"):
        doc(title="   ")


def test_blank_external_id_is_rejected():
    with pytest.raises(InvariantViolation, match="external_id"):
        doc(external_id="")


def test_negative_ordinal_is_rejected():
    with pytest.raises(InvariantViolation, match="ordinal"):
        chunk(ordinal=-1)


@pytest.mark.parametrize("bad", [0, -5])
def test_non_positive_token_count_is_rejected(bad):
    with pytest.raises(InvariantViolation, match="token_count"):
        chunk(token_count=bad)


def test_blank_chunk_text_is_rejected():
    with pytest.raises(InvariantViolation, match="text"):
        chunk(text="  \n ")


def test_citation_label_prefers_the_heading_path():
    assert chunk(heading_path=("Finance", "Q3")).citation_label == "Finance › Q3"


def test_citation_label_falls_back_to_a_human_position():
    assert chunk(ordinal=2, heading_path=()).citation_label == "part 3"


# -- embeddings -----------------------------------------------------------


def test_vector_length_must_match_its_spec():
    with pytest.raises(InvariantViolation, match="dimensions"):
        EmbeddingVector((1.0, 2.0), VOYAGE)


def test_non_finite_values_are_rejected():
    with pytest.raises(InvariantViolation, match="NaN"):
        EmbeddingVector((1.0, float("nan"), 0.0, 0.0), VOYAGE)


def test_dimension_mismatch_refuses_rather_than_truncates():
    """ADR-0002: writing truncated vectors silently is worse than failing."""
    store = EmbeddingSpec("voyage-3-large", 1024)
    provider = EmbeddingSpec("voyage-3-large", 2048)
    with pytest.raises(ConfigurationError, match="do not truncate"):
        store.require_compatible(provider)


def test_same_width_different_model_is_still_incompatible():
    """Equal dimensionality does not make two models' spaces comparable."""
    store = EmbeddingSpec("voyage-3-large", 1024)
    other = EmbeddingSpec("text-embedding-3-large", 1024)
    with pytest.raises(ConfigurationError, match="meaningless"):
        store.require_compatible(other)


def test_cosine_similarity_of_identical_vectors_is_one():
    v = EmbeddingVector((1.0, 0.0, 1.0, 0.0), VOYAGE)
    assert v.cosine_similarity(v) == pytest.approx(1.0)


def test_cosine_similarity_of_orthogonal_vectors_is_zero():
    a = EmbeddingVector((1.0, 0.0, 0.0, 0.0), VOYAGE)
    b = EmbeddingVector((0.0, 1.0, 0.0, 0.0), VOYAGE)
    assert a.cosine_similarity(b) == pytest.approx(0.0)


def test_cosine_similarity_of_a_zero_vector_is_zero_not_a_crash():
    zero = EmbeddingVector((0.0, 0.0, 0.0, 0.0), VOYAGE)
    other = EmbeddingVector((1.0, 0.0, 0.0, 0.0), VOYAGE)
    assert zero.cosine_similarity(other) == 0.0


# -- citations ------------------------------------------------------------


def test_relevance_must_be_a_probability():
    with pytest.raises(InvariantViolation, match=r"\[0, 1\]"):
        Citation(chunk(), relevance=1.4, found_by=(RetrievalMethod.DENSE,))


def test_quoted_span_must_lie_within_the_chunk():
    with pytest.raises(InvariantViolation, match="valid range"):
        Citation(chunk(text="short"), 0.5, (RetrievalMethod.DENSE,), quoted_span=(0, 99))


def test_quoted_span_extracts_the_named_range():
    c = Citation(chunk(text="revenue rose"), 0.9, (RetrievalMethod.DENSE,), quoted_span=(0, 7))
    assert c.quoted_text == "revenue"


def test_citation_without_a_span_quotes_the_whole_chunk():
    c = Citation(chunk(text="revenue rose"), 0.9, (RetrievalMethod.DENSE,))
    assert c.quoted_text == "revenue rose"


def test_citation_exposes_what_the_ui_needs_for_the_source_icon():
    c = Citation(chunk(), 0.9, (RetrievalMethod.DENSE,))
    assert c.mime_type == "application/vnd.google-apps.document"
    assert c.title == "Q3 Revenue"


# -- remaining invariants -------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1024])
def test_embedding_spec_dimensions_must_be_positive(bad):
    with pytest.raises(InvariantViolation, match="dimensions"):
        EmbeddingSpec(model="voyage-3-large", dimensions=bad)


def test_embedding_spec_model_must_not_be_blank():
    with pytest.raises(InvariantViolation, match="model"):
        EmbeddingSpec(model="  ", dimensions=1024)


def test_compatible_specs_pass_the_check():
    spec = EmbeddingSpec("voyage-3-large", 1024)
    spec.require_compatible(EmbeddingSpec("voyage-3-large", 1024))


def test_not_found_carries_the_kind_and_identifier():
    from ragoogle_core.shared.errors import NotFound

    missing = SourceId.new()
    err = NotFound("DocumentSource", missing)
    assert err.kind == "DocumentSource"
    assert err.identifier == missing
    assert str(missing) in str(err)
