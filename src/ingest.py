from src.github_loader import get_repository_files
from src.chunker import split_into_chunks


def ingest_repository(owner, repo):
    files = get_repository_files(owner, repo)

    all_chunks = []

    for file in files:
        chunks = split_into_chunks(file["content"])

        for chunk_number, chunk in enumerate(chunks, start=1):
            all_chunks.append({
                "path": file["path"],
                "chunk_number": chunk_number,
                "content": chunk
            })

    return all_chunks