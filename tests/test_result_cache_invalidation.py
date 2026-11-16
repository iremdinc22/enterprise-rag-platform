"""Ownership-aware invalidation using a memory-only Redis substitute."""
import hashlib
import json
import unittest
from unittest.mock import patch

from redis.exceptions import ConnectionError, TimeoutError
from src.cache import result_cache


def legacy_key(tenant, query="Policy?"):
    # Independent copy of the existing wire/key contract, not a new namespace.
    identity = {"query": query, "model": "gpt-4.1-mini", "retrieval_limit": 5,
                "final_limit": 3, "lambda_value": 0.5}
    return f"rag-result:{tenant}:" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class MemoryRedis:
    def __init__(self, keys=(), *, candidates=None):
        self.values = {key: "synthetic" for key in keys}
        self.candidates = list(keys) if candidates is None else candidates
        self.scans, self.deletes, self.sets, self.gets = [], [], [], []
        self.scan_error = self.delete_error = None

    def scan_iter(self, *, match):
        self.scans.append(match)
        if self.scan_error:
            raise self.scan_error
        # Deliberately return an overbroad result: deletion must not trust MATCH.
        for key in self.candidates:
            yield key

    def delete(self, key):
        self.deletes.append(key)
        if self.delete_error:
            raise self.delete_error
        return int(self.values.pop(key, None) is not None)

    def set(self, key, value, **kwargs):
        self.sets.append((key, kwargs))
        self.values[key] = value

    def get(self, key):
        self.gets.append(key)
        return self.values.get(key)


class ResultCacheInvalidationTests(unittest.TestCase):
    def test_exact_owner_only_preserves_other_tenants_and_embedding_cache(self):
        owned = [legacy_key("acme"), legacy_key("acme", "Second question")]
        preserved = [legacy_key("globex"), legacy_key("ACME"), legacy_key("acme-more"),
                     legacy_key("acme:branch"), "embedding:text-embedding-3-small:" + "a" * 64]
        memory = MemoryRedis(owned + preserved)
        with patch.object(result_cache, "redis_client", memory):
            self.assertEqual(result_cache.invalidate_tenant_results("acme"), 2)
        self.assertEqual(set(memory.values), set(preserved))
        self.assertEqual(memory.deletes, owned)
        self.assertEqual(memory.scans, ["rag-result:*"])

    def test_prefix_collision_never_deletes_descendant_or_ancestor_keys(self):
        keys = [legacy_key("acme"), legacy_key("acme:branch"), legacy_key("acme:branch:child"),
                legacy_key("acmebranch"), legacy_key("ac")]
        memory = MemoryRedis(keys)
        with patch.object(result_cache, "redis_client", memory):
            self.assertEqual(result_cache.invalidate_tenant_results("acme"), 1)
        self.assertEqual(memory.deletes, [keys[0]])
        self.assertEqual(set(memory.values), set(keys[1:]))

    def test_stored_glob_and_escape_tenants_are_not_treated_as_ownership(self):
        keys = [legacy_key("acme"), *[legacy_key(tenant) for tenant in
                ("*", "acme*", "acme?", "[acme]", "acme\\", "acme\\*", "acme:*")]]
        memory = MemoryRedis(keys)
        with patch.object(result_cache, "redis_client", memory):
            self.assertEqual(result_cache.invalidate_tenant_results("acme"), 1)
        self.assertEqual(memory.deletes, [keys[0]])
        self.assertEqual(set(memory.values), set(keys[1:]))

    def test_invalid_tenant_never_scans_or_deletes_any_key(self):
        keys = [legacy_key("acme"), legacy_key("globex"), legacy_key("acme:branch")]
        invalid = ["", " ", "acme:branch", "*", "acme*", "acme?", "[a]", "a\\b", "a\\*",
                   "é", "acme\n", "a" * 129, None, b"acme", True, 123]
        memory = MemoryRedis(keys)
        with patch.object(result_cache, "redis_client", memory):
            for tenant in invalid:
                with self.subTest(type=type(tenant).__name__), self.assertRaisesRegex(ValueError, "^Invalid identifier$"):
                    result_cache.invalidate_tenant_results(tenant)
        self.assertFalse(memory.scans)
        self.assertFalse(memory.deletes)
        self.assertEqual(set(memory.values), set(keys))

    def test_malformed_or_ambiguous_keys_are_preserved(self):
        digest = "a" * 64
        owned = legacy_key("acme")
        malformed = ["rag-result:acme", "rag-result:acme:", "rag-result::" + digest,
                     "rag-result:acme:" + "a" * 63, "rag-result:acme:" + "a" * 65,
                     "rag-result:acme:" + "g" * 64, "rag-result:acme:" + "A" * 64,
                     "rag-result:acme:" + digest + "\n", "rag-result:acme:" + digest + ":extra",
                     "rag-result:acme:" + "a" * 63, "rag-result:acme:" + "a" * 65,
                     "rag-result:acme:" + "g" * 64, "rag-result:acme:" + "A" * 64,
                     "rag-result:acme:" + digest + "\n", "rag-result:acme:" + digest + ":extra",
                     "rag-result:acme::" + digest, "rag-result:acme:branch:" + digest,
                     "rag-result:acme:*", "other:acme:" + digest, "rag-result:acme\x00:" + digest,
                     ("rag-result:acme:" + digest).encode(), b"rag-result:acme:\xff", None, 1]
        memory = MemoryRedis([owned, *malformed])
        with patch.object(result_cache, "redis_client", memory):
            self.assertEqual(result_cache.invalidate_tenant_results("acme"), 1)
        self.assertEqual(memory.deletes, [owned])
        self.assertEqual(set(memory.values), set(malformed))

    def test_existing_key_format_exact_reads_and_ttl_are_unchanged(self):
        memory = MemoryRedis()
        result = {"answer": "Synthetic [1]", "citations": []}
        args = dict(tenant_id="acme", query="Policy?", model="gpt-4.1-mini", retrieval_limit=5,
                    final_limit=3, lambda_value=0.5)
        expected = legacy_key("acme")
        with patch.object(result_cache, "redis_client", memory):
            self.assertEqual(result_cache.build_result_cache_key(**args), expected)
            result_cache.cache_result(**args, result=result)
            self.assertEqual(memory.sets, [(expected, {"ex": 1800})])
            self.assertEqual(result_cache.get_cached_result(**args), result)
            self.assertEqual(memory.gets, [expected])
            memory.candidates = [expected]
            self.assertEqual(result_cache.invalidate_tenant_results("acme"), 1)
            self.assertIsNone(result_cache.get_cached_result(**args))
        self.assertFalse(memory.values)

    def test_scan_and_delete_redis_failures_keep_existing_safe_fallback(self):
        for stage in ("scan", "delete"):
            for error in (ConnectionError("PRIVATE"), TimeoutError("PRIVATE")):
                with self.subTest(stage=stage, error=type(error).__name__):
                    keys = [legacy_key("acme"), legacy_key("globex")]
                    memory = MemoryRedis(keys)
                    setattr(memory, stage + "_error", error)
                    with patch.object(result_cache, "redis_client", memory), patch("builtins.print") as output:
                        self.assertEqual(result_cache.invalidate_tenant_results("acme"), 0)
                    output.assert_called_once_with("Result cache invalidation skipped")
                    self.assertEqual(set(memory.values), set(keys))
                    self.assertNotIn(keys[1], memory.deletes)

    def test_scan_decode_failure_never_deletes_unparseable_keys(self):
        keys = [legacy_key("acme"), legacy_key("globex")]
        memory = MemoryRedis(keys)
        memory.scan_error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
        with patch.object(result_cache, "redis_client", memory), patch("builtins.print") as output:
            self.assertEqual(result_cache.invalidate_tenant_results("acme"), 0)
        self.assertFalse(memory.deletes)
        self.assertEqual(set(memory.values), set(keys))
        output.assert_called_once_with("Result cache invalidation skipped")

    def test_duplicate_scan_keys_and_expired_entries_count_actual_deletes(self):
        owned, other = legacy_key("acme"), legacy_key("globex")
        expired = legacy_key("acme", "Expired")
        memory = MemoryRedis([owned, other], candidates=[owned, owned, expired, other])
        with patch.object(result_cache, "redis_client", memory):
            self.assertEqual(result_cache.invalidate_tenant_results("acme"), 1)
        self.assertEqual(set(memory.values), {other})
        self.assertNotIn(other, memory.deletes)

    def test_partial_scan_failure_does_not_retry_or_delete_other_tenants(self):
        first, second, other = legacy_key("acme"), legacy_key("acme", "Second"), legacy_key("globex")
        memory = MemoryRedis([first, second, other])
        def partial(*, match):
            yield first
            raise ConnectionError("PRIVATE")
        with patch.object(result_cache, "redis_client", memory), \
             patch.object(memory, "scan_iter", side_effect=partial), patch("builtins.print") as output:
            self.assertEqual(result_cache.invalidate_tenant_results("acme"), 0)
        self.assertEqual(memory.deletes, [first])
        self.assertEqual(set(memory.values), {second, other})
        output.assert_called_once_with("Result cache invalidation skipped")

    def test_case_and_maximum_identifier_length_keep_exact_ownership(self):
        for tenant in ("ACME", "a" * 128):
            keys = [legacy_key(tenant), legacy_key("acme"), legacy_key(tenant + ":branch")]
            memory = MemoryRedis(keys)
            with patch.object(result_cache, "redis_client", memory):
                self.assertEqual(result_cache.invalidate_tenant_results(tenant), 1)
            self.assertEqual(memory.deletes, [keys[0]])
            self.assertEqual(set(memory.values), set(keys[1:]))
