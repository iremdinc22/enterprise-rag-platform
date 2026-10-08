import json
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue


DATASET_PATH = Path(__file__).parent / "retrieval_dataset.json"

QDRANT_URL = "http://localhost:6333"
COLLECTION_NAME = "enterprise_documents"


def load_dataset():
    with open(DATASET_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


def inspect_chunks():
    client = QdrantClient(url=QDRANT_URL)
    dataset = load_dataset()

    for case in dataset:
        print("\n" + "=" * 70)
        print(f"CASE: {case['id']}")
        print(f"TENANT: {case['tenant_id']}")
        print(f"QUERY: {case['query']}")
        print("=" * 70)

        tenant_filter = Filter(
            must=[
                FieldCondition(
                    key="tenant_id",
                    match=MatchValue(value=case["tenant_id"]),
                )
            ]
        )

        points = []
        offset = None

        while True:
            batch, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=tenant_filter,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )

            points.extend(batch)

            if offset is None:
                break

        for relevant_id in case["relevant_chunk_ids"]:
            print(f"\nExpected Chunk: {relevant_id}")

            matches = []

            for point in points:
                payload = point.payload or {}

                document_id = payload.get("document_id")
                chunk_id = payload.get("chunk_id")

                actual_id = f"{document_id}:{chunk_id}"

                if actual_id == relevant_id:
                    matches.append(payload)

            if not matches:
                print("NOT FOUND in Qdrant!")
                continue

            for payload in matches:
                print(f"Document ID: {payload.get('document_id')}")
                print(f"Chunk ID: {payload.get('chunk_id')}")
                print(f"Filename: {payload.get('filename')}")
                print(f"Page: {payload.get('page')}")
                print(f"Tenant: {payload.get('tenant_id')}")
                print(f"Text: {payload.get('text')}")


if __name__ == "__main__":
    inspect_chunks()
