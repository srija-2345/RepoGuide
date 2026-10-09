
import re
from urllib.parse import urlparse, quote

import streamlit as st

from src.retrieve import build_repository_index, search_repository
from src.gemini_client import generate_answer


# --------------------------------------------------
# PAGE CONFIGURATION
# --------------------------------------------------

st.set_page_config(
    page_title="RepoGuide | GitHub Knowledge Assistant",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# --------------------------------------------------
# CUSTOM CSS
# --------------------------------------------------

st.markdown(
    """
    <style>
    .stApp {
        background: #f7f9fc;
    }

    .block-container {
        max-width: 1050px;
        padding-top: 2rem;
        padding-bottom: 3rem;
    }

    .hero {
        padding: 1.8rem;
        background: linear-gradient(135deg, #eef2ff, #ffffff);
        border: 1px solid #e0e7ff;
        border-radius: 18px;
        margin-bottom: 1.5rem;
    }

    .hero h1 {
        color: #243b80;
        font-size: 2.3rem;
        margin-bottom: 0.4rem;
    }

    .hero p {
        color: #56627a;
        margin-bottom: 0;
    }

    div[data-testid="stTextInput"] {
        margin-bottom: 0.5rem;
    }

    div[data-testid="stTextInput"] input {
        min-height: 48px;
        border-radius: 10px;
    }

    div[data-testid="stButton"] button {
        min-height: 44px;
        border-radius: 10px;
        font-weight: 600;
    }

    div[data-testid="stChatMessage"] {
        border-radius: 12px;
    }

    .note {
        color: #64748b;
        font-size: 0.85rem;
    }

    footer {
        visibility: hidden;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------
# SESSION STATE
# --------------------------------------------------

defaults = {
    "repo_url": "",
    "repo_owner": "",
    "repo_name": "",
    "repository_data": None,
    "chat_history": [],
    "pending_question": "",
    "repo_input": "",
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# --------------------------------------------------
# URL VALIDATION
# --------------------------------------------------

def parse_repository_url(value):
    value = value.strip()

    if not value:
        raise ValueError("Please enter a GitHub repository URL.")

    if value.startswith("github.com/"):
        value = "https://" + value

    if "://" not in value and "/" in value:
        value = "https://github.com/" + value.strip("/")

    parsed = urlparse(value)

    if parsed.scheme not in ("http", "https"):
        raise ValueError("Please enter a valid GitHub URL.")

    if parsed.netloc.lower() not in ("github.com", "www.github.com"):
        raise ValueError("Please use a URL from github.com.")

    parts = [
        part
        for part in parsed.path.strip("/").split("/")
        if part
    ]

    if len(parts) != 2:
        raise ValueError(
            "Use this format: https://github.com/owner/repository"
        )

    owner, repo = parts

    if repo.endswith(".git"):
        repo = repo[:-4]

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
        raise ValueError("The GitHub owner name is invalid.")

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise ValueError("The repository name is invalid.")

    return owner, repo, f"https://github.com/{owner}/{repo}"


# --------------------------------------------------
# CACHED REPOSITORY INDEX
# --------------------------------------------------

@st.cache_resource(show_spinner=False, max_entries=5)
def load_repository(owner, repo):
    """
    Build and cache the chunks and vector index for a repository.
    """
    chunks, index = build_repository_index(owner, repo)

    if not chunks:
        raise ValueError(
            "No supported source files were found in this repository."
        )

    return {
        "chunks": chunks,
        "index": index,
    }


# --------------------------------------------------
# HEADER
# --------------------------------------------------

st.markdown(
    """
    <div class="hero">
        <h1>📚 RepoGuide</h1>
        <p>
            Your AI-powered GitHub Repository Knowledge Assistant.
            Understand code, explore project structure, and get
            answers grounded in repository files.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------
# REPOSITORY INPUT
# --------------------------------------------------

st.markdown("### Connect a repository")

st.write(
    "Enter a public GitHub repository URL to start exploring its code."
)

with st.container(border=True):
    st.text_input(
        "GitHub repository URL",
        key="repo_input",
        placeholder="https://github.com/owner/repository",
        help=(
            "Example: "
            "https://github.com/srija-2345/gesture-based-brightness-control"
        ),
    )

    connect_clicked = st.button(
        "Connect repository",
        type="primary",
        use_container_width=True,
    )


# --------------------------------------------------
# CONNECT AND INDEX
# --------------------------------------------------

if connect_clicked:
    try:
        owner, repo, normalized_url = parse_repository_url(
            st.session_state.repo_input
        )

        changed_repository = (
            normalized_url != st.session_state.repo_url
        )

        if changed_repository:
            st.session_state.repository_data = None
            st.session_state.chat_history = []
            st.session_state.pending_question = ""

        st.session_state.repo_owner = owner
        st.session_state.repo_name = repo
        st.session_state.repo_url = normalized_url

        # Load repository files and build the search index.
        with st.spinner(
            "Fetching files and building the repository search index..."
        ):
            st.session_state.repository_data = load_repository(
                owner, repo
            )

        st.success("Repository connected successfully!")

    except Exception as error:
        st.session_state.repository_data = None
        st.error(f"Could not connect to the repository: {error}")


# --------------------------------------------------
# CHAT INTERFACE
# --------------------------------------------------

if (
    st.session_state.repository_data is not None
    and st.session_state.repo_url
):
    st.markdown("### Connected repository")

    with st.container(border=True):
        left, right = st.columns([4, 1])

        with left:
            st.markdown(
                f"**📁 {st.session_state.repo_owner}/"
                f"{st.session_state.repo_name}**"
            )

            st.markdown(
                f"[View on GitHub]({st.session_state.repo_url})"
            )

        with right:
            st.success("Ready")

    # --------------------------------------------------
    # GENERIC SUGGESTED QUESTIONS
    # --------------------------------------------------

    st.markdown("### Suggested questions")

    suggested_questions = [
        "What does this repository do?",
        "Explain the main files in this project.",
        "Which libraries and technologies are used?",
        "How does the main functionality work?",
    ]

    col1, col2 = st.columns(2)

    for i, question in enumerate(suggested_questions):
        target_col = col1 if i % 2 == 0 else col2

        with target_col:
            if st.button(
                question,
                key=f"suggestion_{i}",
                use_container_width=True,
            ):
                st.session_state.pending_question = question
                st.rerun()

    # --------------------------------------------------
    # DISPLAY CHAT HISTORY
    # --------------------------------------------------

    st.markdown("### Ask RepoGuide")

    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

            if message.get("sources"):
                with st.expander("View source files"):
                    for source in message["sources"]:
                        st.markdown(source)

    # --------------------------------------------------
    # CHAT INPUT
    # --------------------------------------------------

    typed_question = st.chat_input(
        "Ask anything about this repository..."
    )

    question = (
        st.session_state.pending_question
        or typed_question
    )

    if question:
        st.session_state.pending_question = ""

        st.session_state.chat_history.append(
            {
                "role": "user",
                "content": question,
            }
        )

        with st.chat_message("user"):
            st.markdown(question)

        try:
            with st.chat_message("assistant"):
                with st.spinner(
                    "Searching code and generating your answer..."
                ):
                    repository_data = (
                        st.session_state.repository_data
                    )

                    retrieved_chunks = search_repository(
                        question,
                        repository_data["chunks"],
                        repository_data["index"],
                        top_k=3,
                    )

                    if not retrieved_chunks:
                        answer = (
                            "I couldn't find relevant information in the "
                            "indexed repository files for that question. "
                            "Try asking about a specific file, function, "
                            "class, or feature."
                        )
                    else:
                        answer = generate_answer(
                            question,
                            retrieved_chunks,
                        )

                st.markdown(answer)

                # Build links to the files used as sources.
                sources = []
                seen_paths = set()

                for chunk in retrieved_chunks:
                    path = chunk.get("path")

                    if not path or path in seen_paths:
                        continue

                    seen_paths.add(path)
                    encoded_path = quote(path, safe="/")

                    source_url = (
                        f"{st.session_state.repo_url}"
                        f"/blob/HEAD/{encoded_path}"
                    )

                    sources.append(
                        f"[`{path}`]({source_url})"
                    )

                if sources:
                    with st.expander("View source files"):
                        for source in sources:
                            st.markdown(source)

                st.session_state.chat_history.append(
                    {
                        "role": "assistant",
                        "content": answer,
                        "sources": sources,
                    }
                )

        except Exception as error:
            # Details are written to the app logs, not shown to users.
            print(f"RepoGuide error: {error}")

            error_message = (
                "Sorry, I couldn't process that question. "
                "Please try again. If the problem continues, "
                "check the app logs."
            )

            st.error(error_message)

            st.session_state.chat_history.append(
                {
                    "role": "assistant",
                    "content": error_message,
                    "sources": [],
                }
            )

    # --------------------------------------------------
    # CLEAR CONVERSATION
    # --------------------------------------------------

    if st.session_state.chat_history:
        if st.button("Clear conversation"):
            st.session_state.chat_history = []
            st.session_state.pending_question = ""
            st.rerun()

else:
    st.info(
        "Enter a public GitHub repository URL and click "
        "'Connect repository' to begin."
    )


# --------------------------------------------------
# FOOTER
# --------------------------------------------------

st.markdown("---")

st.markdown(
    """
    <p class="note" style="text-align:center;">
        RepoGuide · AI-powered repository exploration
    </p>
    """,
    unsafe_allow_html=True,
)
