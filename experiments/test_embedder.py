from src.ingestion.embedder import create_embeddings


texts = [
    "Employees receive 20 days of annual leave.",
    "Remote employees must connect through VPN.",
    "Expense reports must be submitted within 30 days."
]


embeddings = create_embeddings(texts)


print("Number of texts:")
print(len(texts))

print("\nNumber of embeddings:")
print(len(embeddings))

print("\nEmbedding dimension:")
print(len(embeddings[0]))

print("\nFirst 5 values of first embedding:")
print(embeddings[0][:5])