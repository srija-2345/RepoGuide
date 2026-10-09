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
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "repoguide_history.db",
)
TABLE_NAME = "repoguide_conversations"


# One shared logo is used in both the sidebar and the main header.
REPOGUIDE_LOGO = r"""<svg viewBox="0 0 100 100" role="img" aria-label="RepoGuide logo" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="rgBookGradient" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#FFFFFF"/>
      <stop offset="100%" stop-color="#DCEAFF"/>
    </linearGradient>
  </defs>
  <path d="M12 48 Q29 42 46 54 L46 80 Q29 68 12 75 Z" fill="url(#rgBookGradient)"/>
  <path d="M88 48 Q71 42 54 54 L54 80 Q71 68 88 75 Z" fill="url(#rgBookGradient)"/>
  <path d="M50 55 L50 82" stroke="#BBD4FF" stroke-width="3" stroke-linecap="round"/>
  <path d="M17 54 Q30 51 40 59 M17 63 Q30 60 40 68 M83 54 Q70 51 60 59 M83 63 Q70 60 60 68" fill="none" stroke="#A7C5FF" stroke-width="2.5" stroke-linecap="round"/>
  <path d="M50 10 L77 23 L74 48 Q67 61 50 68 Q33 61 26 48 L23 23 Z" fill="#0F2F78" stroke="#9FC1FF" stroke-width="2.5"/>
  <path d="M50 17 L70 27 L68 45 Q62 54 50 60 Q38 54 32 45 L30 27 Z" fill="#245ED8"/>
  <path d="M44 31 L36 38 L44 45 M56 31 L64 38 L56 45 M53 29 L47 47" fill="none" stroke="#FFFFFF" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>
  <g class="rg-orbit" fill="none" stroke="#D9E7FF" stroke-width="2" stroke-linecap="round"><path d="M75 37 Q88 39 85 48" stroke-dasharray="3 4"/><circle class="rg-node" cx="85" cy="48" r="3.1" fill="#FFFFFF" stroke="none"/></g>
  <path class="rg-sparkle" d="M80 13 L82 19 L88 21 L82 23 L80 29 L78 23 L72 21 L78 19 Z" fill="#C7DCFF"/>
</svg>"""


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
# SESSION STATE AND CHAT SELECTION
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
            chat["id"] == st.session_state.active_chat_id
            for chat in st.session_state.chats
        )
    ):
        st.session_state.active_chat_id = None

    if not st.session_state.active_chat_id:
        if st.session_state.chats:
            st.session_state.active_chat_id = st.session_state.chats[0]["id"]
        else:
            create_new_chat()

except Exception:
    st.error(
        "Could not load chat history from the database. "
        "Check your database configuration and table."
    )
    st.stop()


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
# PROFESSIONAL BRANDING AND CUSTOM CSS
# --------------------------------------------------

st.markdown("""
<style>
:root {
    --rg-navy: #132448;
    --rg-blue: #2563eb;
    --rg-blue-light: #eff5ff;
    --rg-muted: #64748b;
    --rg-border: #e1e9f5;
}

.stApp {
    background: #f7f9fd;
}

.block-container {
    max-width: 1120px;
    padding-top: 1.45rem;
    padding-bottom: 2.8rem;
}

.rg-sidebar-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 4px 2px 12px 2px;
    margin-bottom: 5px;
}

.rg-sidebar-mark {
    width: 43px;
    height: 43px;
    min-width: 43px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 13px;
    background: linear-gradient(145deg, #3778f6 0%, #1745b8 55%, #6d5dfc 100%);
    box-shadow: 0 5px 13px rgba(37, 99, 235, .22);
    transition: transform .25s ease, box-shadow .25s ease;
}

.rg-sidebar-mark:hover {
    transform: translateY(-2px) scale(1.05);
    box-shadow: 0 8px 18px rgba(37, 99, 235, .32);
}

.rg-sidebar-mark svg { width: 36px; height: 36px; display: block; overflow: visible; }

.rg-sidebar-mark .rg-orbit { transform-origin: 50px 38px; animation: rg-orbit 7s linear infinite; }
.rg-sidebar-mark .rg-sparkle { transform-origin: 80px 21px; animation: rg-sparkle 2.8s ease-in-out infinite; }
.rg-sidebar-mark .rg-node { transform-box: fill-box; transform-origin: center; animation: rg-node-pulse 2.2s ease-in-out infinite; }

.rg-sidebar-subtitle { color: #64748b; font-size: .68rem; line-height: 1.3; margin-top: 3px; }

.rg-sidebar-wordmark {
    font-size: 1.35rem;
    font-weight: 800;
    letter-spacing: -0.7px;
    color: #132448;
    line-height: 1.1;
}

.rg-sidebar-wordmark span { color: #2563eb; }

.hero {
    position: relative;
    display: flex;
    align-items: center;
    gap: 22px;
    overflow: hidden;
    padding: 30px 32px;
    background:
        radial-gradient(circle at 92% 10%, rgba(96, 165, 250, .20), transparent 28%),
        linear-gradient(120deg, #edf4ff 0%, #ffffff 70%);
    border: 1px solid #dce7fb;
    border-radius: 22px;
    margin-bottom: 28px;
    box-shadow: 0 8px 28px rgba(31, 64, 120, .045);
}

.hero::after {
    content: "";
    position: absolute;
    width: 210px;
    height: 210px;
    right: -95px;
    bottom: -145px;
    border: 1px solid rgba(37, 99, 235, .16);
    border-radius: 50%;
    box-shadow:
        0 0 0 22px rgba(37, 99, 235, .035),
        0 0 0 44px rgba(37, 99, 235, .025);
    pointer-events: none;
}

.brand-mark {
    flex: 0 0 88px;
    width: 88px;
    height: 88px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 24px;
    background: linear-gradient(145deg, #3778f6 0%, #1745b8 55%, #6d5dfc 100%);
    box-shadow:
        0 10px 22px rgba(37, 99, 235, .23),
        inset 0 1px 0 rgba(255, 255, 255, .32);
    transition: transform .28s ease, box-shadow .28s ease;
    animation: rg-logo-enter .65s ease-out both;
    cursor: default;
}

.brand-mark:hover {
    transform: translateY(-4px) rotate(-2deg) scale(1.045);
    box-shadow: 0 15px 30px rgba(37, 99, 235, .34),
        inset 0 1px 0 rgba(255, 255, 255, .38);
}

.brand-mark svg {
    width: 68px;
    height: 68px;
    display: block;
    overflow: visible;
}

.brand-mark .rg-orbit {
    transform-origin: 50px 38px;
    animation: rg-orbit 7s linear infinite;
}

.brand-mark .rg-sparkle {
    transform-origin: 80px 21px;
    animation: rg-sparkle 2.8s ease-in-out infinite;
}

.brand-mark .rg-node {
    transform-box: fill-box;
    transform-origin: center;
    animation: rg-node-pulse 2.2s ease-in-out infinite;
}

@keyframes rg-logo-enter {
    from { opacity: 0; transform: translateY(8px) scale(.94); }
    to { opacity: 1; transform: translateY(0) scale(1); }
}

@keyframes rg-orbit {
    to { transform: rotate(360deg); }
}

@keyframes rg-sparkle {
    0%, 100% { opacity: .8; transform: scale(1); }
    50% { opacity: 1; transform: scale(1.16); }
}

@keyframes rg-node-pulse {
    0%, 100% { opacity: .75; }
    50% { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
    .brand-mark, .brand-mark svg *, .brand-mark .rg-orbit,
    .brand-mark .rg-sparkle, .brand-mark .rg-node,
    .rg-sidebar-mark .rg-orbit, .rg-sidebar-mark .rg-sparkle,
    .rg-sidebar-mark .rg-node {
        animation: none !important;
        transition: none !important;
    }
}

.hero-copy {
    position: relative;
    z-index: 1;
    min-width: 0;
}

.brand-title {
    margin: 0 0 8px 0;
    color: var(--rg-navy);
    font-size: clamp(2rem, 4vw, 2.65rem);
    line-height: 1.12;
    font-weight: 800;
    letter-spacing: -1.5px;
}

.brand-title .brand-accent {
    color: var(--rg-blue);
}

.brand-kicker {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    margin-bottom: 10px;
    padding: 5px 10px;
    border: 1px solid #d8e5ff;
    border-radius: 999px;
    background: rgba(255, 255, 255, .76);
    color: #315aab;
    font-size: .76rem;
    font-weight: 700;
    letter-spacing: .45px;
    text-transform: uppercase;
}

.brand-kicker-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #2e72f5;
    box-shadow: 0 0 0 3px rgba(46, 114, 245, .12);
}

.brand-description {
    max-width: 680px;
    margin: 0;
    color: #5c6e8d;
    font-size: 1rem;
    line-height: 1.75;
}

div[data-testid="stTextInput"] input {
    min-height: 48px;
    border-radius: 10px;
}

div[data-testid="stButton"] button {
    min-height: 42px;
    border-radius: 10px;
    font-weight: 600;
    transition: border-color .15s ease, box-shadow .15s ease;
}

div[data-testid="stButton"] button:hover {
    border-color: #93b4fa;
    box-shadow: 0 3px 12px rgba(37, 99, 235, .08);
}

div[data-testid="stChatMessage"] {
    border-radius: 12px;
}

section[data-testid="stSidebar"] {
    border-right: 1px solid #e2e8f0;
}

.note {
    color: #64748b;
    font-size: .85rem;
}

footer {
    visibility: hidden;
}

@media (max-width: 600px) {
    .hero {
        align-items: flex-start;
        gap: 15px;
        padding: 21px 18px;
        border-radius: 17px;
    }

    .brand-mark {
        flex-basis: 58px;
        width: 58px;
        height: 58px;
        border-radius: 17px;
    }

    .brand-mark svg {
        width: 47px;
        height: 47px;
    }

    .brand-title {
        font-size: 1.8rem;
        letter-spacing: -1px;
    }

    .brand-description {
        font-size: .9rem;
        line-height: 1.55;
    }

    .brand-kicker {
        font-size: .65rem;
    }
}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------
# SIDEBAR CHAT HISTORY
# --------------------------------------------------

with st.sidebar:
    # Compact matching logo and title in the top-left sidebar corner.
    st.markdown(f"""
    <div class="rg-sidebar-brand">
      <div class="rg-sidebar-mark" title="RepoGuide">{REPOGUIDE_LOGO}</div>
      <div>
        <div class="rg-sidebar-wordmark">Repo<span>Guide</span></div>
        <div class="rg-sidebar-subtitle">AI Repository Assistant</div>
      </div>
    </div>
    """, unsafe_allow_html=True)
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

    # Storage status label intentionally omitted from the UI.

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
# PROFESSIONAL HEADER WITH EMBEDDED SVG LOGO
# --------------------------------------------------

st.markdown(f"""
<div class="hero">
    <div class="brand-mark" aria-label="RepoGuide logo">{REPOGUIDE_LOGO}</div>
    <div class="hero-copy">
        <div class="brand-kicker"><span class="brand-kicker-dot"></span>AI-powered developer tool</div>
        <h1 class="brand-title">Repo<span class="brand-accent">Guide</span></h1>
        <p class="brand-description">Your AI-powered GitHub Repository Assistant. Connect a repository, explore its codebase, understand project structure, and ask questions with answers grounded in repository files.</p>
    </div>
</div>
""", unsafe_allow_html=True)

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
st.write("Enter a public GitHub repository URL to explore its code.")

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

    question = st.session_state.pending_question or typed_question

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
                        answer = generate_answer(question, retrieved_chunks)

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
    st.info("Connect a public GitHub repository to start a conversation.")


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
