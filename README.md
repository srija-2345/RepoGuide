# RepoGuide — GitHub Repository Knowledge Assistant

RepoGuide is an AI-powered assistant designed to help developers understand GitHub repositories by answering questions about their source code and documentation.

## Planned Features

- Retrieve repository files from GitHub.
- Split source code and documentation into meaningful chunks.
- Generate embeddings and search for relevant content using FAISS.
- Use Retrieval-Augmented Generation (RAG) to answer questions grounded in repository content.
- Provide source file references to help users verify answers.
- Offer an interactive interface for asking repository-related questions.

## Technology Stack

- **Language:** Python
- **API Integration:** GitHub REST API
- **Embeddings:** Sentence Transformers
- **Vector Search:** FAISS
- **LLM Integration:** API-based language model
- **User Interface:** Streamlit

## Project Status

Currently in the initial setup phase. Features will be implemented and tested incrementally.

## Goals

- Improve understanding of unfamiliar codebases.
- Retrieve relevant code and documentation efficiently.
- Generate answers grounded in repository evidence.
- Evaluate retrieval quality and answer reliability.