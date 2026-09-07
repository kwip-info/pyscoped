"""Tests for search / indexing — SearchIndex, SearchResult."""

import pytest

from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.objects.search import (
    IndexEntry,
    SearchIndex,
    SearchResult,
    index_entry_from_row,
)


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
    return alice, bob


@pytest.fixture
def obj_manager(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def search(sqlite_backend):
    return SearchIndex(sqlite_backend)


# -----------------------------------------------------------------------
# IndexEntry model
# -----------------------------------------------------------------------

class TestIndexEntry:

    def test_from_row(self):
        row = {
            "id": "e1", "object_id": "o1", "object_type": "Doc",
            "owner_id": "alice", "field_name": "title",
            "content": "hello world", "scope_id": None,
            "indexed_at": "2026-01-01T00:00:00+00:00",
        }
        entry = index_entry_from_row(row)
        assert entry.object_id == "o1"
        assert entry.field_name == "title"
        assert entry.content == "hello world"

    def test_frozen(self):
        from scoped.types import now_utc

        entry = IndexEntry(
            id="e1", object_id="o1", object_type="Doc",
            owner_id="alice", field_name="title",
            content="text", scope_id=None, indexed_at=now_utc(),
        )
        with pytest.raises(AttributeError):
            entry.content = "other"


# -----------------------------------------------------------------------
# SearchIndex — indexing
# -----------------------------------------------------------------------

class TestSearchIndexing:

    def test_index_object(self, search, obj_manager, principals):
        alice, _ = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "Hello World", "body": "This is a document."},
        )

        count = search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "Hello World", "body": "This is a document."},
        )
        assert count == 2
        assert search.is_indexed(obj.id)

    def test_index_specific_fields(self, search, obj_manager, principals):
        alice, _ = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "Hello", "body": "World", "secret": "hidden"},
        )

        count = search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "Hello", "body": "World", "secret": "hidden"},
            fields=["title", "body"],
        )
        assert count == 2
        assert "secret" not in search.get_indexed_fields(obj.id)

    def test_index_skips_none_values(self, search, obj_manager, principals):
        alice, _ = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "Hello", "body": None},
        )

        count = search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "Hello", "body": None},
        )
        assert count == 1

    def test_index_non_string_values(self, search, obj_manager, principals):
        alice, _ = principals
        obj, _ = obj_manager.create(
            object_type="Record", owner_id=alice.id,
            data={"count": 42, "active": True, "tags": ["python", "search"]},
        )

        count = search.index_object(
            object_id=obj.id, object_type="Record", owner_id=alice.id,
            data={"count": 42, "active": True, "tags": ["python", "search"]},
        )
        assert count == 3

    def test_reindex_replaces(self, search, obj_manager, principals):
        alice, _ = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "Old Title"},
        )

        search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "Old Title"},
        )
        search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "New Title"},
        )

        fields = search.get_indexed_fields(obj.id)
        assert fields == ["title"]
        assert search.index_count() == 1

    def test_remove_object(self, search, obj_manager, principals):
        alice, _ = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "Remove Me"},
        )

        search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "Remove Me"},
        )
        assert search.is_indexed(obj.id)

        removed = search.remove_object(obj.id)
        assert removed == 1
        assert not search.is_indexed(obj.id)

    def test_remove_nonexistent(self, search):
        assert search.remove_object("nope") == 0

    def test_index_count(self, search, obj_manager, principals):
        alice, _ = principals
        assert search.index_count() == 0

        obj1, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "A", "body": "B"},
        )
        search.index_object(
            object_id=obj1.id, object_type="Doc", owner_id=alice.id,
            data={"title": "A", "body": "B"},
        )
        assert search.index_count() == 2

    def test_index_with_scope(self, search, obj_manager, principals):
        alice, _ = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "Scoped Doc"},
        )

        search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "Scoped Doc"},
            scope_id="scope-123",
        )
        assert search.is_indexed(obj.id)

    def test_index_dict_value(self, search, obj_manager, principals):
        alice, _ = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"meta": {"author": "Alice", "dept": "Engineering"}},
        )

        count = search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"meta": {"author": "Alice", "dept": "Engineering"}},
        )
        assert count == 1

    def test_index_empty_string_skipped(self, search, obj_manager, principals):
        alice, _ = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "", "body": "content"},
        )

        count = search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "", "body": "content"},
        )
        assert count == 1  # empty string skipped


# -----------------------------------------------------------------------
# SearchIndex — searching
# -----------------------------------------------------------------------

class TestSearchQuery:

    def test_search_basic(self, search, obj_manager, principals):
        alice, _ = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "quantum computing research"},
        )
        search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "quantum computing research"},
        )

        results = search.search("quantum", principal_id=alice.id)
        assert len(results) == 1
        assert results[0].object_id == obj.id
        assert results[0].field_name == "title"

    def test_search_isolation(self, search, obj_manager, principals):
        alice, bob = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "alice private document"},
        )
        search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "alice private document"},
        )

        # Alice can find it
        assert len(search.search("private", principal_id=alice.id)) == 1
        # Bob cannot
        assert len(search.search("private", principal_id=bob.id)) == 0

    def test_search_by_object_type(self, search, obj_manager, principals):
        alice, _ = principals
        doc, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "meeting notes"},
        )
        task, _ = obj_manager.create(
            object_type="Task", owner_id=alice.id,
            data={"title": "meeting preparation"},
        )
        search.index_object(
            object_id=doc.id, object_type="Doc", owner_id=alice.id,
            data={"title": "meeting notes"},
        )
        search.index_object(
            object_id=task.id, object_type="Task", owner_id=alice.id,
            data={"title": "meeting preparation"},
        )

        results = search.search("meeting", principal_id=alice.id, object_type="Doc")
        assert len(results) == 1
        assert results[0].object_type == "Doc"

    def test_search_by_scope(self, search, obj_manager, principals):
        alice, _ = principals
        obj1, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "scope A document"},
        )
        obj2, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "scope B document"},
        )
        search.index_object(
            object_id=obj1.id, object_type="Doc", owner_id=alice.id,
            data={"title": "scope A document"}, scope_id="scope-a",
        )
        search.index_object(
            object_id=obj2.id, object_type="Doc", owner_id=alice.id,
            data={"title": "scope B document"}, scope_id="scope-b",
        )

        results = search.search("document", principal_id=alice.id, scope_id="scope-a")
        assert len(results) == 1
        assert results[0].object_id == obj1.id

    def test_search_multiple_results(self, search, obj_manager, principals):
        alice, _ = principals
        for i in range(5):
            obj, _ = obj_manager.create(
                object_type="Doc", owner_id=alice.id,
                data={"title": f"machine learning paper {i}"},
            )
            search.index_object(
                object_id=obj.id, object_type="Doc", owner_id=alice.id,
                data={"title": f"machine learning paper {i}"},
            )

        results = search.search("machine", principal_id=alice.id)
        assert len(results) == 5

    def test_search_limit(self, search, obj_manager, principals):
        alice, _ = principals
        for i in range(10):
            obj, _ = obj_manager.create(
                object_type="Doc", owner_id=alice.id,
                data={"title": f"searchable document {i}"},
            )
            search.index_object(
                object_id=obj.id, object_type="Doc", owner_id=alice.id,
                data={"title": f"searchable document {i}"},
            )

        results = search.search("searchable", principal_id=alice.id, limit=3)
        assert len(results) == 3

    def test_search_empty_query(self, search, principals):
        alice, _ = principals
        assert search.search("", principal_id=alice.id) == []
        assert search.search("   ", principal_id=alice.id) == []

    def test_search_no_results(self, search, principals):
        alice, _ = principals
        assert search.search("nonexistent", principal_id=alice.id) == []

    def test_search_snippet_truncated(self, search, obj_manager, principals):
        alice, _ = principals
        long_text = "searchword " + "x" * 300
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"body": long_text},
        )
        search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"body": long_text},
        )

        results = search.search("searchword", principal_id=alice.id)
        assert len(results) == 1
        assert len(results[0].snippet) <= 200

    def test_search_result_has_rank(self, search, obj_manager, principals):
        alice, _ = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "testing rank values"},
        )
        search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "testing rank values"},
        )

        results = search.search("testing", principal_id=alice.id)
        assert len(results) == 1
        assert isinstance(results[0].rank, float)


# -----------------------------------------------------------------------
# SearchIndex — search_with_visibility
# -----------------------------------------------------------------------

class TestSearchWithVisibility:

    def test_search_with_visibility(self, search, obj_manager, principals):
        alice, bob = principals
        obj_a, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "shared report"},
        )
        obj_b, _ = obj_manager.create(
            object_type="Doc", owner_id=bob.id,
            data={"title": "other report"},
        )
        search.index_object(
            object_id=obj_a.id, object_type="Doc", owner_id=alice.id,
            data={"title": "shared report"},
        )
        search.index_object(
            object_id=obj_b.id, object_type="Doc", owner_id=bob.id,
            data={"title": "other report"},
        )

        # Bob can see both via visibility set
        results = search.search_with_visibility(
            "report",
            principal_id=bob.id,
            visible_object_ids=[obj_a.id, obj_b.id],
        )
        assert len(results) == 2

    def test_search_with_empty_visibility(self, search, principals):
        alice, _ = principals
        results = search.search_with_visibility(
            "anything", principal_id=alice.id, visible_object_ids=[],
        )
        assert results == []

    def test_search_with_visibility_empty_query(self, search, principals):
        alice, _ = principals
        results = search.search_with_visibility(
            "", principal_id=alice.id, visible_object_ids=["obj-1"],
        )
        assert results == []

    def test_search_with_visibility_type_filter(self, search, obj_manager, principals):
        alice, _ = principals
        doc, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "document about testing"},
        )
        task, _ = obj_manager.create(
            object_type="Task", owner_id=alice.id,
            data={"title": "task about testing"},
        )
        search.index_object(
            object_id=doc.id, object_type="Doc", owner_id=alice.id,
            data={"title": "document about testing"},
        )
        search.index_object(
            object_id=task.id, object_type="Task", owner_id=alice.id,
            data={"title": "task about testing"},
        )

        results = search.search_with_visibility(
            "testing",
            principal_id=alice.id,
            visible_object_ids=[doc.id, task.id],
            object_type="Task",
        )
        assert len(results) == 1
        assert results[0].object_type == "Task"


# -----------------------------------------------------------------------
# SearchIndex — count_results
# -----------------------------------------------------------------------

class TestSearchCount:

    def test_count_results(self, search, obj_manager, principals):
        alice, _ = principals
        for i in range(3):
            obj, _ = obj_manager.create(
                object_type="Doc", owner_id=alice.id,
                data={"title": f"countable item {i}"},
            )
            search.index_object(
                object_id=obj.id, object_type="Doc", owner_id=alice.id,
                data={"title": f"countable item {i}"},
            )

        assert search.count_results("countable", principal_id=alice.id) == 3

    def test_count_empty_query(self, search, principals):
        alice, _ = principals
        assert search.count_results("", principal_id=alice.id) == 0

    def test_count_with_type_filter(self, search, obj_manager, principals):
        alice, _ = principals
        doc, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "filterable doc"},
        )
        task, _ = obj_manager.create(
            object_type="Task", owner_id=alice.id,
            data={"title": "filterable task"},
        )
        search.index_object(
            object_id=doc.id, object_type="Doc", owner_id=alice.id,
            data={"title": "filterable doc"},
        )
        search.index_object(
            object_id=task.id, object_type="Task", owner_id=alice.id,
            data={"title": "filterable task"},
        )

        assert search.count_results("filterable", principal_id=alice.id, object_type="Doc") == 1

    def test_count_isolation(self, search, obj_manager, principals):
        alice, bob = principals
        obj, _ = obj_manager.create(
            object_type="Doc", owner_id=alice.id,
            data={"title": "isolated content"},
        )
        search.index_object(
            object_id=obj.id, object_type="Doc", owner_id=alice.id,
            data={"title": "isolated content"},
        )

        assert search.count_results("isolated", principal_id=alice.id) == 1
        assert search.count_results("isolated", principal_id=bob.id) == 0
