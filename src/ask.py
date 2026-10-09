
from retrieve import build_repository_index, search_repository
from gemini_client import generate_answer


def ask_repository(owner, repo, question):
    print("Loading repository and building search index...")

    chunks, index = build_repository_index(owner, repo)

    print("Searching for relevant code...")

    retrieved_chunks = search_repository(
        question,
        chunks,
        index,
        top_k=3
    )

    if not retrieved_chunks:
        return "No relevant repository content was found."

    print("Generating answer with Gemini...")

    answer = generate_answer(question, retrieved_chunks)

    return answer


if __name__ == "__main__":
    owner = "srija-2345"
    repo = "gesture-based-brightness-control"

    question = input("Ask a question about the repository: ")

    answer = ask_repository(owner, repo, question)

    print("\nRepoGuide Answer:\n")
    print(answer)
