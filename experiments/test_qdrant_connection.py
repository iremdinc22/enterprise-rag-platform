from src.vector_store.qdrant_store import (
    client,
    create_collection,
    COLLECTION_NAME
)


create_collection()

collection = client.get_collection(
    COLLECTION_NAME
)

print("Collection:")
print(COLLECTION_NAME)

print("\nCollection status:")
print(collection.status)

print("\nVector configuration:")
print(collection.config.params.vectors)