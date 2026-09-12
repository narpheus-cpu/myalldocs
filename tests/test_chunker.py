from indexer.chunker import chunk_text


def test_chunks_near_target_and_keep_order():
    text = "\n\n".join(f"문단 {i} " + "가" * 400 for i in range(20))
    chunks = chunk_text(text, 1000, 0)
    assert len(chunks) > 3
    assert all(item["charCount"] <= 1000 for item in chunks)
    assert [c["chunkId"] for c in chunks] == list(range(1, len(chunks) + 1))


def test_long_paragraph_is_split():
    chunks = chunk_text("가" * 2100, 700, 0)
    assert [len(c["text"]) for c in chunks] == [700, 700, 700]


def test_empty_text_has_no_chunks():
    assert chunk_text(" \n ") == []
