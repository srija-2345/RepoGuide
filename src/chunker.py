
def split_into_chunks(text, chunk_size=1000, overlap=150):
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        start += chunk_size - overlap

    return chunks


if __name__ == "__main__":
    sample_text = """
    RepoGuide reads files from GitHub.
    It splits source code into smaller chunks.
    These chunks will later be converted into embeddings.
    FAISS retrieves the most relevant chunks for a question.
    """

    chunks = split_into_chunks(sample_text, chunk_size=80, overlap=10)

    print("Number of chunks:", len(chunks))

    for i, chunk in enumerate(chunks, start=1):
        print(f"\nChunk {i}:")
        print(chunk)
