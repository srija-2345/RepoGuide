
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import requests
import streamlit as st

from src.retrieve import build_repository_index, search_repository
from src.gemini_client import generate_answer


# --------------------------------------------------
# PAGE CONFIGURATION
# --------------------------------------------------

st.set_page_config(
    page_title="RepoGuide | GitHub Knowledge Assistant",
    page_icon="📘",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "repoguide_history.db",
)
TABLE_NAME = "repoguide_conversations"


# --------------------------------------------------
# DATABASE CONFIGURATION
# --------------------------------------------------

def get_setting(name):
    try:
        value = st.secrets.get(name, "")
        if value:
            return str(value).strip()
    except Exception:
        pass

    return os.getenv(name, "").strip()


SUPABASE_URL = get_setting("SUPABASE_URL").rstrip("/")
SUPABASE_KEY = (
    get_setting("SUPABASE_SECRET_KEY")
    or get_setting("SUPABASE_SERVICE_ROLE_KEY")
)
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)


def db_request(method, path="", **kwargs):
    """Send a request to the Supabase REST API."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    response = requests.request(
        method,
        f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}{path}",
        headers=headers,
        timeout=30,
        **kwargs,
    )
    response.raise_for_status()

    if response.text:
        return response.json()

    return None


def init_local_db():
    with sqlite3.connect(DB_FILE, timeout=20) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                title TEXT NOT NULL,
                repo_url TEXT NOT NULL DEFAULT '',
                repo_owner TEXT NOT NULL DEFAULT '',
                repo_name TEXT NOT NULL DEFAULT '',
                messages TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_workspace_updated
            ON conversations(workspace_id, updated_at DESC)
        """)


init_local_db()


# --------------------------------------------------
# WORKSPACE
# --------------------------------------------------

workspace_id = st.query_params.get("workspace", "")

if not re.fullmatch(r"[a-f0-9-]{36}", workspace_id):
    workspace_id = str(uuid.uuid4())
    st.query_params["workspace"] = workspace_id

st.session_state["workspace_id"] = workspace_id


# --------------------------------------------------
# DATABASE OPERATIONS
# --------------------------------------------------

def save_conversation(chat):
    """Save a conversation and its messages."""
    row = {
        "id": chat["id"],
        "workspace_id": workspace_id,
        "title": chat.get("title", "New chat"),
        "repo_url": chat.get("repo_url", ""),
        "repo_owner": chat.get("repo_owner", ""),
        "repo_name": chat.get("repo_name", ""),
        "messages": chat.get("messages", []),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if USE_SUPABASE:
        db_request(
            "POST",
            "?on_conflict=id",
            params={"select": "id"},
            json=row,
        )
    else:
        with sqlite3.connect(DB_FILE, timeout=20) as connection:
            connection.execute("""
                INSERT INTO conversations
                (id, workspace_id, title, repo_url, repo_owner,
                 repo_name, messages, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    repo_url=excluded.repo_url,
                    repo_owner=excluded.repo_owner,
                    repo_name=excluded.repo_name,
                    messages=excluded.messages,
                    updated_at=excluded.updated_at
            """, (
                row["id"],
                row["workspace_id"],
                row["title"],
                row["repo_url"],
                row["repo_owner"],
                row["repo_name"],
                json.dumps(row["messages"]),
                row["updated_at"],
            ))


def load_conversations():
    """Load conversations for the current workspace."""
    if USE_SUPABASE:
        rows = db_request(
            "GET",
            "",
            params={
                "select": (
                    "id,workspace_id,title,repo_url,repo_owner,"
                    "repo_name,messages,updated_at"
                ),
                "workspace_id": f"eq.{workspace_id}",
                "order": "updated_at.desc",
            },
        )
        return rows or []

    with sqlite3.connect(DB_FILE, timeout=20) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("""
            SELECT * FROM conversations
            WHERE workspace_id = ?
            ORDER BY updated_at DESC
        """, (workspace_id,)).fetchall()

    chats = []

    for row in rows:
        chat = dict(row)
        chat["messages"] = json.loads(chat["messages"] or "[]")
        chats.append(chat)

    return chats


def delete_conversation(chat_id):
    if USE_SUPABASE:
        db_request(
            "DELETE",
            "",
            params={
                "id": f"eq.{chat_id}",
                "workspace_id": f"eq.{workspace_id}",
            },
        )
    else:
        with sqlite3.connect(DB_FILE, timeout=20) as connection:
            connection.execute("""
                DELETE FROM conversations
                WHERE id = ? AND workspace_id = ?
            """, (chat_id, workspace_id))


# --------------------------------------------------
# SESSION STATE
# --------------------------------------------------

if "repository_data" not in st.session_state:
    st.session_state.repository_data = None

if "loaded_repo_url" not in st.session_state:
    st.session_state.loaded_repo_url = ""

if "pending_question" not in st.session_state:
    st.session_state.pending_question = ""

if "repo_input" not in st.session_state:
    st.session_state.repo_input = ""

if "active_chat_id" not in st.session_state:
    st.session_state.active_chat_id = None

if "db_error" not in st.session_state:
    st.session_state.db_error = ""


def refresh_chats():
    st.session_state.chats = load_conversations()


def get_active_chat():
    for chat in st.session_state.chats:
        if chat["id"] == st.session_state.active_chat_id:
            return chat

    return None


def create_new_chat():
    chat = {
        "id": str(uuid.uuid4()),
        "workspace_id": workspace_id,
        "title": "New chat",
        "repo_url": "",
        "repo_owner": "",
        "repo_name": "",
        "messages": [],
    }

    save_conversation(chat)
    refresh_chats()

    st.session_state.active_chat_id = chat["id"]
    st.session_state.repository_data = None
    st.session_state.loaded_repo_url = ""
    st.session_state.pending_question = ""
    st.session_state.repo_input = ""


try:
    if "chats" not in st.session_state:
        refresh_chats()

    if (
        st.session_state.active_chat_id
        and not any(
            c["id"] == st.session_state.active_chat_id
            for c in st.session_state.chats
        )
    ):
        st.session_state.active_chat_id = None

    if not st.session_state.active_chat_id:
        if st.session_state.chats:
            st.session_state.active_chat_id = (
                st.session_state.chats[0]["id"]
            )
        else:
            create_new_chat()

except Exception:
    st.error(
        "Could not load chat history from the database. "
        "Check your database configuration and table."
    )
    st.stop()


# --------------------------------------------------
# REPOSITORY URL VALIDATION
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

    if parsed.netloc.lower() not in (
        "github.com", "www.github.com"
    ):
        raise ValueError("Please use a URL from github.com.")

    parts = [
        part for part in parsed.path.strip("/").split("/") if part
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
    chunks, index = build_repository_index(owner, repo)

    if not chunks:
        raise ValueError(
            "No supported source files were found in this repository."
        )

    return {"chunks": chunks, "index": index}


# --------------------------------------------------
# PROFESSIONAL THEME AND CREATIVE LOGO
# --------------------------------------------------

st.markdown("""
<style>
.stApp {
    background: #f7f9fd;
    color: #172554;
}

.block-container {
    max-width: 1180px;
    padding-top: 1.4rem;
    padding-bottom: 2.5rem;
}

section[data-testid="stSidebar"] {
    background: #ffffff;
    border-right: 1px solid #e2e8f0;
}

.brand-header {
    display: flex;
    align-items: center;
    gap: 17px;
    padding: 24px 28px;
    margin-bottom: 26px;
    border: 1px solid #dbeafe;
    border-radius: 20px;
    background:
        radial-gradient(
            circle at 90% 10%,
            rgba(96, 165, 250, 0.18),
            transparent 32%
        ),
        linear-gradient(120deg, #eff6ff 0%, #ffffff 75%);
    box-shadow: 0 8px 28px rgba(30, 64, 175, 0.045);
}

.brand-logo {
    flex: 0 0 76px;
    width: 76px;
    height: 76px;
    display: flex;
    align-items: center;
    justify-content: center;
}

.brand-logo svg {
    width: 76px;
    height: 76px;
    filter: drop-shadow(0 5px 7px rgba(37, 99, 235, 0.16));
}

.brand-title {
    margin: 0;
    color: #14264b;
    font-size: 2.25rem;
    line-height: 1.15;
    font-weight: 800;
    letter-spacing: -1.2px;
}

.brand-title .brand-accent {
    color: #2563eb;
}

.brand-tagline {
    margin-top: 9px;
    color: #64748b;
    font-size: 1rem;
    line-height: 1.6;
}

.brand-pill {
    display: inline-block;
    margin-top: 11px;
    padding: 5px 10px;
    border: 1px solid #bfdbfe;
    border-radius: 20px;
    background: #eff6ff;
    color: #1d4ed8;
    font-size: 0.76rem;
    font-weight: 650;
    letter-spacing: 0.2px;
}

div[data-testid="stTextInput"] input {
    min-height: 47px;
    border-radius: 10px;
}

div[data-testid="stButton"] button {
    min-height: 42px;
    border-radius: 10px;
    font-weight: 600;
    transition: all 0.15s ease;
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

@media (max-width: 600px) {
    .brand-header {
        padding: 18px;
        gap: 12px;
    }

    .brand-logo,
    .brand-logo svg {
        width: 56px;
        height: 56px;
    }

    .brand-logo {
        flex-basis: 56px;
    }

    .brand-title {
        font-size: 1.75rem;
    }

    .brand-tagline {
        font-size: 0.88rem;
    }
}
</style>
""", unsafe_allow_html=True)


def render_brand_header():
    """Render the RepoGuide logo and product heading."""
    logo_svg = """
    <svg xmlns="http://www.w3.org/2000/svg"
         viewBox="0 0 100 100"
         role="img"
         aria-label="RepoGuide code book logo">
        <defs>
            <linearGradient id="bookBlue"
                            x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#60A5FA"/>
                <stop offset="100%" stop-color="#2563EB"/>
            </linearGradient>
            <linearGradient id="shieldBlue"
                            x1="0" y1="0" x2="0.9" y2="1">
                <stop offset="0%" stop-color="#1E40AF"/>
                <stop offset="100%" stop-color="#2563EB"/>
            </linearGradient>
        </defs>

        <!-- Open book -->
        <path d="M12 45
                 C25 40 37 43 50 52
                 C63 43 75 40 88 45
                 L88 78
                 C74 73 62 77 50 86
                 C38 77 26 73 12 78 Z"
              fill="url(#bookBlue)"/>

        <path d="M19 51
                 C30 48 39 51 47 58
                 L47 76
                 C38 69 29 67 19 69 Z"
              fill="#FFFFFF"/>

        <path d="M53 58
                 C61 51 70 48 81 51
                 L81 69
                 C71 67 62 69 53 76 Z"
              fill="#DBEAFE"/>

        <!-- Code shield -->
        <path d="M50 8
                 L78 20
                 L78 43
                 C78 59 66 69 50 77
                 C34 69 22 59 22 43
                 L22 20 Z"
              fill="url(#shieldBlue)"
              stroke="#FFFFFF"
              stroke-width="3"/>

        <path d="M50 16
                 L70 25
                 L70 42
                 C70 54 61 62 50 68
                 C39 62 30 54 30 42
                 L30 25 Z"
              fill="none"
              stroke="#93C5FD"
              stroke-width="1.5"/>

        <!-- Code brackets -->
        <path d="M43 33 L36 40 L43 47"
              fill="none"
              stroke="#FFFFFF"
              stroke-width="3.5"
              stroke-linecap="round"
              stroke-linejoin="round"/>

        <path d="M57 33 L64 40 L57 47"
              fill="none"
              stroke="#FFFFFF"
              stroke-width="3.5"
              stroke-linecap="round"
              stroke-linejoin="round"/>

        <path d="M53 30 L47 50"
              fill="none"
              stroke="#BFDBFE"
              stroke-width="3"
              stroke-linecap="round"/>
    </svg>
    """

    import html

    safe_logo = logo_svg.strip()
    tagline = html.escape(
        "Your AI-powered GitHub Repository Knowledge Assistant."
    )

    st.markdown(
        f"""
        <div class="brand-header">
            <div class="brand-logo">
                {safe_logo}
            </div>
            <div>
                <h1 class="brand-title">
                    Repo<span class="brand-accent">Guide</span>
                </h1>
                <div class="brand-tagline">{tagline}<br>
                    Explore code. Understand projects. Build smarter.
                </div>
                <span class="brand-pill">
                    AI-POWERED REPOSITORY EXPLORATION
                </span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# --------------------------------------------------
# SIDEBAR CHAT HISTORY
# --------------------------------------------------

with st.sidebar:
    st.markdown("## 📘 RepoGuide")
    st.caption("Your repository conversations")

    if st.button(
        "+ New chat",
        type="primary",
        use_container_width=True,
    ):
        try:
            create_new_chat()
            st.rerun()
        except Exception as error:
            st.error(f"Could not create chat: {error}")

    st.divider()
    st.markdown("### Chat history")

    for chat in st.session_state.chats:
        title = chat.get("title") or "New chat"

        if len(title) > 32:
            title = title[:29] + "..."

        prefix = (
            "● " if chat["id"] == st.session_state.active_chat_id
            else ""
        )

        if st.button(
            prefix + title,
            key=f"chat_{chat['id']}",
            use_container_width=True,
        ):
            st.session_state.active_chat_id = chat["id"]
            st.session_state.repository_data = None
            st.session_state.loaded_repo_url = ""
            st.session_state.pending_question = ""
            st.rerun()

    st.divider()

    # Storage status text intentionally omitted.

    active_for_delete = get_active_chat()

    if active_for_delete and st.button(
        "Delete current chat",
        use_container_width=True,
    ):
        try:
            delete_conversation(active_for_delete["id"])
            st.session_state.active_chat_id = None
            st.session_state.repository_data = None
            st.session_state.loaded_repo_url = ""
            refresh_chats()

            if st.session_state.chats:
                st.session_state.active_chat_id = (
                    st.session_state.chats[0]["id"]
                )
            else:
                create_new_chat()

            st.rerun()

        except Exception as error:
            st.error(f"Could not delete chat: {error}")


# --------------------------------------------------
# HEADER
# --------------------------------------------------

render_brand_header()


# --------------------------------------------------
# ACTIVE CHAT AND REPOSITORY RESTORATION
# --------------------------------------------------

active_chat = get_active_chat()

if active_chat and active_chat.get("repo_url"):
    st.session_state.repo_url = active_chat["repo_url"]
    st.session_state.repo_owner = active_chat["repo_owner"]
    st.session_state.repo_name = active_chat["repo_name"]
    st.session_state.repo_input = active_chat["repo_url"]

    try:
        if (
            st.session_state.repository_data is None
            or st.session_state.loaded_repo_url != active_chat["repo_url"]
        ):
            with st.spinner("Restoring repository index..."):
                st.session_state.repository_data = load_repository(
                    active_chat["repo_owner"],
                    active_chat["repo_name"],
                )
                st.session_state.loaded_repo_url = active_chat["repo_url"]

    except Exception as error:
        st.session_state.repository_data = None
        st.error(f"Could not restore repository: {error}")

else:
    st.session_state.repo_url = ""
    st.session_state.repo_owner = ""
    st.session_state.repo_name = ""


# --------------------------------------------------
# REPOSITORY INPUT
# --------------------------------------------------

st.markdown("### Connect a repository")

st.write(
    "Enter a public GitHub repository URL to explore its code."
)

with st.container(border=True):
    st.text_input(
        "GitHub repository URL",
        key="repo_input",
        placeholder="https://github.com/owner/repository",
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

        with st.spinner("Building repository search index..."):
            repository_data = load_repository(owner, repo)

        active_chat = get_active_chat()

        active_chat["repo_url"] = normalized_url
        active_chat["repo_owner"] = owner
        active_chat["repo_name"] = repo

        if not active_chat["messages"]:
            active_chat["title"] = f"{owner}/{repo}"

        st.session_state.repository_data = repository_data
        st.session_state.loaded_repo_url = normalized_url

        save_conversation(active_chat)
        refresh_chats()

        st.success("Repository connected successfully!")

    except Exception as error:
        st.error(f"Could not connect to repository: {error}")


# --------------------------------------------------
# CHAT INTERFACE
# --------------------------------------------------

active_chat = get_active_chat()

repository_ready = bool(
    active_chat
    and active_chat.get("repo_url")
    and st.session_state.repository_data is not None
    and st.session_state.loaded_repo_url == active_chat["repo_url"]
)

if repository_ready:
    st.markdown("### Connected repository")

    with st.container(border=True):
        left, right = st.columns([4, 1])

        with left:
            st.markdown(
                f"**{active_chat['repo_owner']}/"
                f"{active_chat['repo_name']}**"
            )
            st.markdown(
                f"[View on GitHub]({active_chat['repo_url']})"
            )

        with right:
            st.success("Ready")

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
                key=f"suggestion_{active_chat['id']}_{i}",
                use_container_width=True,
            ):
                st.session_state.pending_question = question
                st.rerun()

    st.markdown("### Ask RepoGuide")

    for message in active_chat["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

            if message.get("sources"):
                with st.expander("View source files"):
                    for source in message["sources"]:
                        st.markdown(source)

    typed_question = st.chat_input(
        "Ask anything about this repository..."
    )

    question = (
        st.session_state.pending_question or typed_question
    )

    if question:
        st.session_state.pending_question = ""

        active_chat["messages"].append({
            "role": "user",
            "content": question,
        })

        if active_chat["title"] == "New chat":
            active_chat["title"] = question[:45]

        try:
            save_conversation(active_chat)
            refresh_chats()

            with st.chat_message("user"):
                st.markdown(question)

            with st.chat_message("assistant"):
                with st.spinner(
                    "Searching code and generating your answer..."
                ):
                    repository_data = st.session_state.repository_data

                    retrieved_chunks = search_repository(
                        question,
                        repository_data["chunks"],
                        repository_data["index"],
                        top_k=3,
                    )

                    if not retrieved_chunks:
                        answer = (
                            "I couldn't find relevant information in "
                            "the indexed repository files. Try asking "
                            "about a specific file or function."
                        )
                    else:
                        answer = generate_answer(
                            question,
                            retrieved_chunks,
                        )

                st.markdown(answer)

                sources = []
                seen_paths = set()

                for chunk in retrieved_chunks:
                    path = chunk.get("path")

                    if not path or path in seen_paths:
                        continue

                    seen_paths.add(path)
                    encoded_path = quote(path, safe="/")
                    source_url = (
                        f"{active_chat['repo_url']}"
                        f"/blob/HEAD/{encoded_path}"
                    )
                    sources.append(f"[`{path}`]({source_url})")

                if sources:
                    with st.expander("View source files"):
                        for source in sources:
                            st.markdown(source)

            active_chat["messages"].append({
                "role": "assistant",
                "content": answer,
                "sources": sources,
            })

            save_conversation(active_chat)
            refresh_chats()

        except Exception as error:
            print(f"RepoGuide error: {error}")

            error_message = (
                "Sorry, I couldn't complete your request. "
                "Check the app logs for details."
            )

            active_chat["messages"].append({
                "role": "assistant",
                "content": error_message,
                "sources": [],
            })

            try:
                save_conversation(active_chat)
                refresh_chats()
            except Exception as save_error:
                print(f"Chat save error: {save_error}")

            st.error(error_message)

elif active_chat and active_chat.get("repo_url"):
    st.info("Restoring the repository. Please wait.")

else:
    st.info(
        "Connect a public GitHub repository to start a conversation."
    )


# --------------------------------------------------
# CLEAR CURRENT CONVERSATION
# --------------------------------------------------

active_chat = get_active_chat()

if active_chat and active_chat["messages"]:
    if st.button("Clear conversation"):
        active_chat["messages"] = []
        active_chat["title"] = (
            f"{active_chat['repo_owner']}/{active_chat['repo_name']}"
            if active_chat.get("repo_url")
            else "New chat"
        )

        try:
            save_conversation(active_chat)
            refresh_chats()
            st.rerun()

        except Exception as error:
            st.error(f"Could not clear conversation: {error}")


# --------------------------------------------------
# FOOTER
# --------------------------------------------------

st.markdown("---")
st.markdown(
    '<p class="note" style="text-align:center;">'
    'RepoGuide · AI-powered repository exploration'
    '</p>',
    unsafe_allow_html=True,
)
