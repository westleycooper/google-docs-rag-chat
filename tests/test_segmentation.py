"""Text segmentation: recovering document structure for citation labels."""

import pytest
from tests.fakes import FakeTokenizer

from ragoogle_core.application import segment


@pytest.fixture
def tokenizer():
    return FakeTokenizer()


async def test_empty_text_yields_no_segments(tokenizer):
    assert await segment("", tokenizer) == []
    assert await segment("   \n\n  ", tokenizer) == []


async def test_paragraphs_become_separate_segments(tokenizer):
    segments = await segment("First para here.\n\nSecond para here.", tokenizer)
    assert [s.text for s in segments] == ["First para here.", "Second para here."]


async def test_markdown_headings_build_a_trail(tokenizer):
    segments = await segment("# Finance\n\nRevenue rose.\n\n## Q3\n\nDetail here.", tokenizer)
    assert segments[0].heading_path == ("Finance",)
    assert segments[1].heading_path == ("Finance", "Q3")


async def test_a_shallower_heading_truncates_the_trail(tokenizer):
    segments = await segment("# A\n\n## B\n\nunder b\n\n# C\n\nunder c", tokenizer)
    assert segments[0].heading_path == ("A", "B")
    assert segments[1].heading_path == ("C",)


async def test_a_heading_with_body_on_the_same_block_keeps_both(tokenizer):
    segments = await segment("# Finance\nRevenue rose sharply.", tokenizer)
    assert len(segments) == 1
    assert segments[0].heading_path == ("Finance",)
    assert segments[0].text == "Revenue rose sharply."


async def test_a_heading_alone_contributes_no_segment(tokenizer):
    segments = await segment("# Finance\n\nBody text.", tokenizer)
    assert len(segments) == 1


async def test_an_unstyled_heading_from_export_is_recovered(tokenizer):
    """Google Docs export loses the style but keeps the line."""
    segments = await segment("Executive Summary\n\nRevenue rose sharply.", tokenizer)
    assert segments[0].heading_path == ("Executive Summary",)
    assert segments[0].text == "Revenue rose sharply."


@pytest.mark.parametrize(
    "line",
    [
        "This sentence ends with a full stop.",  # punctuation
        "this starts lowercase",  # not title-ish
        "A line that goes on and on and on and on and on and on and on and on",  # too long
        "Word " * 20,  # too many words
    ],
)
async def test_ordinary_prose_is_not_mistaken_for_a_heading(tokenizer, line):
    """A false positive splits a chunk that should have stayed whole."""
    segments = await segment(f"{line}\n\nFollowing body text.", tokenizer)
    assert segments[0].heading_path == ()
    assert len(segments) == 2


async def test_a_single_paragraph_document_has_no_heading(tokenizer):
    segments = await segment("Just one ordinary paragraph of prose here.", tokenizer)
    assert len(segments) == 1
    assert segments[0].heading_path == ()


async def test_token_counts_come_from_the_tokenizer(tokenizer):
    segments = await segment("one two three four five", tokenizer)
    assert segments[0].token_count == 5


async def test_a_zero_count_is_floored_to_one(tokenizer):
    """TextSegment forbids a non-positive count; a tokeniser may still return 0."""

    class ZeroTokenizer:
        async def count(self, text):
            return 0

        async def count_batch(self, texts):
            return [0] * len(texts)

    segments = await segment("some text here", ZeroTokenizer())
    assert segments[0].token_count == 1


async def test_counting_happens_in_one_batched_call():
    """A 200-paragraph document must not be 200 round-trips."""
    calls = []

    class CountingTokenizer:
        async def count(self, text):
            calls.append(1)
            return len(text.split())

        async def count_batch(self, texts):
            calls.append(len(texts))
            return [len(t.split()) for t in texts]

    body = "\n\n".join(f"Paragraph number {i} of prose." for i in range(50))
    await segment(body, CountingTokenizer())
    assert calls == [50]


async def test_headings_with_no_body_at_all_yield_nothing(tokenizer):
    assert await segment("# Only\n\n## Headings", tokenizer) == []


async def test_leading_and_trailing_blank_lines_are_ignored(tokenizer):
    """Exported documents routinely begin and end with whitespace."""
    segments = await segment("\n\n\nReal content here.\n\n\n", tokenizer)
    assert len(segments) == 1
    assert segments[0].text == "Real content here."


async def test_runs_of_blank_lines_between_paragraphs_do_not_create_empties(tokenizer):
    segments = await segment("First.\n\n\n\n\nSecond.", tokenizer)
    assert [s.text for s in segments] == ["First.", "Second."]
