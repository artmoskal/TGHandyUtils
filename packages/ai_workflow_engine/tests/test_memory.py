import pytest

from ai_workflow_engine import (
    EvidenceRef,
    FullReplayMemory,
    ImageEvictingMemory,
    InMemoryMemoryStore,
    MemoryNamespace,
    MemoryRecord,
    MemoryStore,
    resolve_agent_memory,
)

pytestmark = pytest.mark.unit


NS_A = MemoryNamespace("mageqa", "tenant-1", "https://shop.example", "finding_history")
NS_B = MemoryNamespace("mageqa", "tenant-2", "https://shop.example", "finding_history")


def test_memory_namespace_is_named_and_accepts_mageqa_shape():
    record = MemoryRecord(
        namespace=("mageqa", "tenant-1", "https://shop.example", "learned_flow"),
        key="checkout",
    )

    assert isinstance(record.namespace, MemoryNamespace)
    assert record.namespace.product == "mageqa"
    assert record.namespace.tenant == "tenant-1"
    assert record.namespace.subject == "https://shop.example"
    assert record.namespace.kind == "learned_flow"

    with pytest.raises(ValueError):
        MemoryRecord(namespace=("mageqa", "tenant-1", "https://shop.example"), key="checkout")


def test_in_memory_memory_store_is_namespaced_and_deterministic():
    store = InMemoryMemoryStore()
    failed = MemoryRecord(
        namespace=NS_A,
        key="test-b",
        value={"test": "checkout", "status": "failed", "reason": "timeout"},
        metadata={"kind": "test_result", "status": "failed"},
        source="run-1",
        evidence_refs=[
            EvidenceRef(role="log", uri="evidence://run-1/checkout.log", media_type="text/plain")
        ],
    )
    passed = MemoryRecord(
        namespace=NS_A,
        key="test-a",
        value={"test": "login", "status": "passed"},
        metadata={"kind": "test_result", "status": "passed"},
        source="run-1",
    )

    store.put(failed)
    store.put(passed)

    assert isinstance(store, MemoryStore)
    assert [record.key for record in store.search(NS_A)] == ["test-a", "test-b"]
    assert [record.key for record in store.search(tuple(NS_A))] == ["test-a", "test-b"]
    assert store.get(NS_B, "test-a") is None
    assert [record.key for record in store.search(NS_A, metadata_filter={"status": "failed"})] == ["test-b"]
    assert [record.key for record in store.search(NS_A, query="timeout")] == ["test-b"]
    assert store.delete(NS_A, "test-a") is True
    assert store.delete(NS_A, "test-a") is False
    assert [record.key for record in store.search(NS_A)] == ["test-b"]


def test_in_memory_memory_store_idempotency_key_reuse_is_loud():
    store = InMemoryMemoryStore()
    record = MemoryRecord(namespace=NS_A, key="attempt-1", value={"status": "failed"})

    assert store.put(record, idempotency_key="write-1") == record
    assert store.put(record, idempotency_key="write-1") == record

    with pytest.raises(ValueError, match="idempotency key reused"):
        store.put(
            MemoryRecord(namespace=NS_A, key="attempt-1", value={"status": "passed"}),
            idempotency_key="write-1",
        )


def test_agent_memory_config_resolution_is_explicit_and_loud():
    assert isinstance(resolve_agent_memory(None), FullReplayMemory)
    assert isinstance(resolve_agent_memory("full_replay"), FullReplayMemory)
    assert isinstance(resolve_agent_memory({"mode": "image_evicting", "keep_last_images": 2}), ImageEvictingMemory)

    for alias_or_unknown in ("full", "default", "image_eviction", "semantic_search"):
        with pytest.raises(ValueError, match="Unknown agent memory mode"):
            resolve_agent_memory(alias_or_unknown)

    with pytest.raises(ValueError, match="requires mode"):
        resolve_agent_memory({"keep_last_images": 1})
