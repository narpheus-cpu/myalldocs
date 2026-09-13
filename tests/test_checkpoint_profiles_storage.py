import json

from indexer.checkpoint import CheckpointStore
from indexer.profiles import choose_profile
from indexer.pipeline import korean_genre
from indexer.storage import RepositoryStorage


def test_resume_fixture_starts_at_44(tmp_path):
    store=CheckpointStore(tmp_path)
    store.save("book",{"completedChunks":43,"totalChunks":100,"nextChunk":44,"chunkAnalyses":[{"chunkId":i} for i in range(1,44)]})
    restored=store.load("book")
    remaining=[i for i in range(1,101) if i not in {x["chunkId"] for x in restored["chunkAnalyses"]}]
    assert restored["nextChunk"]==44 and remaining[0]==44


def test_low_confidence_profile_falls_back_to_unknown():
    config={"profiles":{"academic":{},"unknown":{}}}
    assert choose_profile({"documentType":"academic","confidence":.2},config)[0]=="unknown"
    assert choose_profile({"documentType":"academic","confidence":.2},config,"academic")[0]=="academic"


def test_catalog_update_is_idempotent(tmp_path):
    data=tmp_path/"data";data.mkdir();(data/"catalog.json").write_text('{"schemaVersion":1,"books":[]}',encoding="utf-8")
    storage=RepositoryStorage(tmp_path);entry={"bookId":"x","title":"한 권","author":"가"}
    storage.update_catalog(entry);storage.update_catalog(entry)
    assert len(json.loads((data/"catalog.json").read_text(encoding="utf-8"))["books"])==1


def test_override_loader_accepts_explicit_management_structure(tmp_path):
    data=tmp_path/"data";data.mkdir();(data/"metadata-overrides.json").write_text('{"byDriveFileId":{"id":{"title":"고침"}}}',encoding="utf-8")
    assert RepositoryStorage(tmp_path).overrides()["id"]["title"]=="고침"


def test_genre_is_always_presented_in_korean():
    assert korean_genre("novel", "fiction") == "소설"
    assert korean_genre("", "history_biography") == "역사·전기"
    assert korean_genre("여행기", "unknown") == "여행기"


def test_completed_manifest_is_found_by_source_content_not_metadata(tmp_path):
    manifest_dir = tmp_path / "data" / "books" / "old-id"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(json.dumps({
        "bookId": "old-id",
        "title": "사용자가 바꾼 작품명",
        "author": "사용자가 바꾼 작가명",
        "indexStatus": "COMPLETE",
        "source": {"sha256": "same-source-hash"},
    }, ensure_ascii=False), encoding="utf-8")
    storage = RepositoryStorage(tmp_path)
    found = storage.completed_manifest_by_sha256("same-source-hash")
    assert found and found["bookId"] == "old-id"
    assert storage.completed_manifest_by_sha256("different-source-hash") is None
