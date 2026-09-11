from src.ingestion.embedder import create_embedding
from src.vector_store.qdrant_store import search_chunks


query = "How many vacation days do employees receive?"

query_embedding = create_embedding(query)

results = search_chunks(
    query_vector=query_embedding,
    limit=3
)


print("\n========== QUERY ==========\n")
print(query)

print("\n========== SEARCH RESULTS ==========\n")

for rank, result in enumerate(results, start=1):
    print(f"Rank: {rank}")
    print(f"Point ID: {result.id}")
    print(f"Score: {result.score:.4f}")
    print(f"Chunk ID: {result.payload['chunk_id']}")
    print(f"Page: {result.payload['page']}")
    print(f"Text: {result.payload['text']}")
    print("-" * 60)