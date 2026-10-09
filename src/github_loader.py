import io
import os
import zipfile
import requests
import streamlit as st
from urllib.parse import quote

ALLOWED_EXTENSIONS = {
    ".py", ".md", ".txt", ".js", ".ts",
    ".java", ".html", ".css", ".json"
}

IGNORED_PARTS = {
    "node_modules", "venv", ".venv", "__pycache__",
    ".git", "dist", "build", ".next"
}

MAX_FILE_SIZE = 500 * 1024
MAX_FILES = 100
MAX_ZIP_SIZE = 50 * 1024 * 1024


@st.cache_data(ttl=1800, show_spinner=False)
def get_repository_files(owner, repo):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "RepoGuide",
        "Accept": "application/vnd.github+json"
    })

    last_error = None

    try:
        # Try the two most common default branch names.
        for branch in ("main", "master"):
            url = (
                f"https://codeload.github.com/"
                f"{quote(owner, safe='')}/{quote(repo, safe='')}"
                f"/zip/refs/heads/{quote(branch, safe='')}"
            )

            try:
                response = session.get(url, timeout=45)

                if response.status_code == 404:
                    last_error = f"Branch '{branch}' was not found."
                    continue

                response.raise_for_status()

                if len(response.content) > MAX_ZIP_SIZE:
                    raise RuntimeError(
                        "Repository ZIP exceeds the 50 MB download limit."
                    )

                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    result = []

                    for item in archive.infolist():
                        if item.is_dir():
                            continue

                        # GitHub ZIP paths start with a root folder.
                        parts = item.filename.split("/")
                        if len(parts) < 2:
                            continue

                        relative_path = "/".join(parts[1:])
                        path_parts = relative_path.split("/")

                        if any(part in IGNORED_PARTS for part in path_parts):
                            continue

                        _, extension = os.path.splitext(relative_path.lower())

                        if extension not in ALLOWED_EXTENSIONS:
                            continue

                        if item.file_size > MAX_FILE_SIZE:
                            continue

                        if item.file_size == 0:
                            continue

                        # Avoid unexpectedly large decompressed files.
                        if item.file_size > MAX_FILE_SIZE:
                            continue

                        try:
                            content = archive.read(item).decode(
                                "utf-8", errors="replace"
                            )
                        except (KeyError, RuntimeError, zipfile.BadZipFile):
                            continue

                        result.append({
                            "path": relative_path,
                            "content": content
                        })

                        if len(result) >= MAX_FILES:
                            break

                if not result:
                    raise RuntimeError(
                        "No supported source files were found. "
                        "Check the repository and its file extensions."
                    )

                return result

            except requests.RequestException as error:
                last_error = str(error)

        raise RuntimeError(
            "Could not download this public repository. "
            "RepoGuide tried the 'main' and 'master' branches. "
            "The repository may use another default branch, be private, "
            "or be unavailable. "
            f"Details: {last_error}"
        )

    finally:
        session.close()