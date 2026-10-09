
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

documents = [
    "The application uses hand gestures to control the mouse.",
    "The program calculates student grades using Python.",
    "A pinch gesture between fingers triggers a mouse click.",
    "The application uses OpenCV to process webcam images."
]

embeddings = model.encode(documents)
embeddings = np.array(embeddings, dtype="float32")

index = faiss.IndexFlatL2(embeddings.shape[1])
index.add(embeddings)

question = "How does the application perform mouse clicking?"
question_embedding = model.encode([question])
question_embedding = np.array(question_embedding, dtype="float32")

distances, indices = index.search(question_embedding, k=2)

print("Question:", question)
print("\nMost relevant documents:")

for i in indices[0]:
    print("-", documents[i])
