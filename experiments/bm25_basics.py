import re
import math


documents = [
    "Employees receive 20 days of annual leave per year.",
    "Remote employees must connect using the corporate VPN.",
    "Error code AUTH-9187 indicates an expired VPN certificate.",
    "PostgreSQL databases are backed up every night.",
    "Employees may work remotely up to two days per week."
]


def tokenize(text):
    return re.findall(r"\b[\w-]+\b", text.lower())


tokenized_documents = [
    tokenize(document)
    for document in documents
]


for document in tokenized_documents:
    print(document)


query = "employees vpn"
query_tokens = tokenize(query)

for index, document in enumerate(tokenized_documents, start=1):
    for term in query_tokens:
        term_frequency = document.count(term)

        print(
            f"Document {index} | "
            f"Term: {term} | "
            f"TF: {term_frequency}"
        )


total_documents = len(tokenized_documents)

print("\nIDF values:")

for term in query_tokens:
    document_frequency = sum(
        1
        for document in tokenized_documents
        if term in document
    )

    idf = math.log(
        1 + (
            total_documents - document_frequency + 0.5
        ) / (
            document_frequency + 0.5
        )
    )

    print(
        f"Term: {term} | "
        f"DF: {document_frequency} | "
        f"IDF: {idf:.4f}"
    )

document_lengths = [
    len(document)
    for document in tokenized_documents
]

average_document_length = (
    sum(document_lengths) / len(document_lengths)
)

print("\nDocument lengths:")

for index, length in enumerate(document_lengths, start=1):
    print(
        f"Document {index} | "
        f"Length: {length}"
    )

print(
    f"Average document length: "
    f"{average_document_length:.2f}"
)


print("\nK1 / TF Saturation Experiment:")

tf_values = [1, 2, 5, 10]
k1_values = [0.5, 1.5, 3.0]

idf = 1.0

for experiment_k1 in k1_values:
    print(f"\nk1 = {experiment_k1}")

    for tf in tf_values:
        tf_score = idf * (
            (tf * (experiment_k1 + 1))
            /
            (tf + experiment_k1)
        )

        print(
            f"TF: {tf:2d} | "
            f"Score: {tf_score:.4f}"
        )