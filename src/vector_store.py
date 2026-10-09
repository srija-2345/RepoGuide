
import faiss
import numpy as np


def create_vector_store(embeddings):
    embeddings = np.asarray(embeddings, dtype="float32")

    if embeddings.ndim != 2 or embeddings.shape[0] == 0:
        raise ValueError(
            "Cannot create a vector store without embeddings."
        )

    if embeddings.shape[1] == 0:
        raise ValueError("Embedding vectors cannot be empty.")

    if not np.isfinite(embeddings).all():
        raise ValueError("Embeddings contain invalid numeric values.")

    # Normalize vectors so inner product represents cosine similarity.
    faiss.normalize_L2(embeddings)

    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)

    return index


def search_vector_store(index, query_embedding, top_k=3):
    if index.ntotal == 0:
        return (
            np.array([], dtype="float32"),
            np.array([], dtype="int64"),
        )

    query_embedding = np.asarray(
        query_embedding, dtype="float32"
    ).reshape(1, -1)

    if not np.isfinite(query_embedding).all():
        raise ValueError("Query embedding contains invalid values.")

    if query_embedding.shape[1] != index.d:
        raise ValueError(
            "Query embedding dimensions do not match the index."
        )

    faiss.normalize_L2(query_embedding)

    top_k = max(1, min(int(top_k), index.ntotal))

    scores, indices = index.search(query_embedding, top_k)

    return scores[0], indices[0]
