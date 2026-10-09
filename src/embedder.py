
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


def generate_embeddings(texts):
    if not texts:
        return model.encode([], convert_to_numpy=True)

    return model.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True
    )


if __name__ == "__main__":
    sample_texts = [
        "The application controls screen brightness using hand gestures.",
        "The program detects a user's hand through a webcam.",
        "FAISS searches for similar text using embeddings."
    ]

    embeddings = generate_embeddings(sample_texts)

    print("Number of sentences:", len(sample_texts))
    print("Embedding shape:", embeddings.shape)
    print("First embedding (first 10 numbers):")
    print(embeddings[0][:10])
