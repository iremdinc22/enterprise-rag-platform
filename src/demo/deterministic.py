"""Opt-in demo adapters. No learned model, transport, provider or corpus fixtures."""
import hashlib
import math
import re
import sys
from types import ModuleType, SimpleNamespace

from src.demo.providers import DisabledOpenAI, ProviderDisabled

EMBEDDING_MODEL = 'demo-hash-v1'
GENERATION_MODEL = 'demo-extractive-v1'
DIMENSIONS = 1536
LABEL = 'Simulated AI — deterministic local demo'
STOP_WORDS = frozenset('a an the is are was were what which who how much many does do did can could would please tell me about of for to in on and or it its this that with from have has be my our their'.split())


def terms(text):
    return set(re.findall(r'[^\W_]+', text.casefold(), flags=re.UNICODE)) - STOP_WORDS


def embedding(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Invalid demo embedding input')
    vector = [0.0] * DIMENSIONS
    for word in re.findall(r'[^\W_]+', text.casefold(), flags=re.UNICODE):
        digest = hashlib.sha256(word.encode('utf-8')).digest()
        index = int.from_bytes(digest[:8], 'big') % DIMENSIONS
        vector[index] += 1.0 if digest[8] & 1 else -1.0
    norm = math.sqrt(sum(value*value for value in vector))
    if not norm:
        vector[0] = 1.0
        norm = 1.0
    return [value/norm for value in vector]


class DeterministicOpenAI(DisabledOpenAI):
    """Only the existing async embedding interface is allowed; all else blocks."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.embeddings = SimpleNamespace(create=self.create_embeddings)

    async def create_embeddings(self, *, model, input, timeout=None, **kwargs):
        if model != EMBEDDING_MODEL or kwargs:
            raise ProviderDisabled('Unsupported deterministic embedding operation')
        texts = [input] if isinstance(input, str) else input
        if not isinstance(texts, (list, tuple)) or not texts or len(texts) > 200:
            raise ValueError('Invalid demo embedding batch')
        return SimpleNamespace(data=[SimpleNamespace(embedding=embedding(text)) for text in texts], usage=None)


def rerank(query, candidates, limit=3):
    if not isinstance(query, str) or not query.strip() or limit <= 0:
        raise ValueError('Invalid demo reranking request')
    query_terms = terms(query)
    ranked = [{**candidate, 'reranker_score':float(len(query_terms & terms(candidate['text'])))}
              for candidate in candidates]
    return sorted(ranked, key=lambda item: (-item['reranker_score'], str(item.get('point_id',''))))[:limit]


async def generate_answer(query, context, model, *, citations):
    # Authorization derives exclusively from structured groups, never formatted text.
    from src.citations.citation_builder import authorized_citation_ids
    from src.generation.generator import GeneratedAnswer, ABSTENTION_MESSAGE, validate_generated_answer
    from src.rag.execution import check_deadline
    check_deadline()
    if model != GENERATION_MODEL or not isinstance(query, str) or not query.strip():
        raise ValueError('Invalid deterministic generation request')
    authorized = authorized_citation_ids(citations)
    query_terms = terms(query)
    matches = []
    for group in citations:
        for sentence in re.split(r'(?<=[.!?])\s+|\n+', group['text']):
            sentence = sentence.strip()
            # Never copy embedded citation markers into the generated answer.
            # Conservative exact-word evidence matching is intentionally not semantic.
            if (query_terms and query_terms <= terms(sentence) and sentence
                    and len(sentence) <= 500 and len(sentence.split()) <= 80
                    and '[' not in sentence and ']' not in sentence):
                matches.append((len(sentence), group['citation_id'], sentence))
    if matches:
        _, citation_id, sentence = min(matches)
        result = GeneratedAnswer(answer=f'{LABEL}\nSource excerpt: “{sentence}” [{citation_id}]',
                                 answered=True, citation_ids=[citation_id])
    else:
        result = GeneratedAnswer(answer=ABSTENTION_MESSAGE, answered=False, citation_ids=[])
    if not validate_generated_answer(result, authorized):
        raise ValueError('Invalid deterministic citation result')
    check_deadline()
    return result


def install():
    """Called only after strict demo activation, before existing pipeline imports."""
    import openai
    openai.AsyncOpenAI = DeterministicOpenAI
    # Sync/Azure/unknown operations remain the disabled fail-closed implementation.
    # Install only the model boundary; keep context selection/RRF/MMR unchanged.
    module = ModuleType('src.retrieval.reranker')
    module.rerank = rerank
    sys.modules[module.__name__] = module
    from src.generation import generator
    generator.generate_answer = generate_answer
    from src.retrieval import bm25_retriever
    original_search = bm25_retriever.search_bm25

    async def safe_search(query, tenant_id, limit=3):
        chunks = await bm25_retriever.load_chunks(tenant_id)
        if not chunks or not any(bm25_retriever.tokenize(chunk['text']) for chunk in chunks):
            return []
        return await original_search(query=query, tenant_id=tenant_id, limit=limit)

    bm25_retriever.search_bm25 = safe_search
