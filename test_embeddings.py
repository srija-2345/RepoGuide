from sentence_transformers import SentenceTransformer

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

texts = [
    "The application detects hand gestures to control the mouse.",
    "The program uses Python to calculate student grades.",
    "A pinch gesture triggers a mouse click."
]

embeddings = model.encode(texts)

print("Number of texts:", len(texts))
print("Embedding shape:", embeddings.shape)
print("First embedding dimensions:", embeddings[0][:5])
