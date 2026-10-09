
from src.ingest import ingest_repository
from src.embedder import generate_embeddings
from src.vector_store import (
    create_vector_store,
    search_vector_store,
)


def build_repository_index(owner, repo):
    chunks = ingest_repository(owner, repo)

    if not chunks:
        raise ValueError(
            "No supported source files were found in this repository."
        )

    texts = [chunk["content"] for chunk in chunks]
    embeddings = generate_embeddings(texts)

    index = create_vector_store(embeddings)

    return chunks, index


def search_repository(question, chunks, index, top_k=5):
    if not question or not question.strip():
        return []

    if not chunks or index.ntotal == 0:
        return []

    query_embedding = generate_embeddings([question])[0]

    scores, indices = search_vector_store(
        index,
        query_embedding,
        top_k,
    )

    results = []

    for score, idx in zip(scores, indices):
        idx = int(idx)

        if idx < 0 or idx >= len(chunks):
            continue

        chunk = chunks[idx]

        results.append({
            "path": chunk["path"],
            "chunk_number": chunk["chunk_number"],
            "content": chunk["content"],
            "score": float(score),
        })

    return results
