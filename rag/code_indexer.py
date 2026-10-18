import argparse
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from rag.call_resolver import Resolution, resolve_repository
from rag.file_classifier import MAX_TEXT_FILE_BYTES, SKIPPED_DIRS, language_of
from rag.symbol_extractor import ParsedModule, extract_module


MAX_PYTHON_FILE_BYTES = 1_000_000


# One row of the files table, with the parsed module for Python files.
@dataclass
class IndexedFile:
    path: str
    language: str
    content_hash: str
    content: str | None = None  # plain text of non-Python files, for grep
    parse_error: str | None = None
    module: ParsedModule | None = None


# Everything the writer stores for one commit of the repo.
@dataclass
class CodeIndex:
    files: list[IndexedFile]
    resolution: Resolution


def _repo_files(repo_root: Path):
    for directory, subdirectories, file_names in os.walk(repo_root):
        subdirectories[:] = sorted(name for name in subdirectories if name not in SKIPPED_DIRS)
        for name in sorted(file_names):
            path = Path(directory) / name
            if not path.is_symlink():
                yield path, path.relative_to(repo_root).as_posix()


# A Python file always gets a row; a text file only when it is small, UTF-8 and not binary.
def _index_file(path: Path, relative_path: str, language: str) -> IndexedFile | None:
    size = path.stat().st_size
    if language != "python" and size > MAX_TEXT_FILE_BYTES:
        return None
    data = path.read_bytes()
    content_hash = hashlib.sha256(data).hexdigest()
    if language == "python" and size > MAX_PYTHON_FILE_BYTES:
        return IndexedFile(relative_path, language, content_hash, parse_error=f"larger than {MAX_PYTHON_FILE_BYTES} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return IndexedFile(relative_path, language, content_hash, parse_error="not UTF-8") if language == "python" else None
    if "\x00" in text:
        return None  # Postgres TEXT rejects NUL bytes
    if language != "python":
        return IndexedFile(relative_path, language, content_hash, content=text)
    module = extract_module(relative_path, text)
    return IndexedFile(relative_path, language, content_hash, parse_error=module.parse_error, module=module)


# Parses a checkout into files, symbols and resolved edges; no database, no LLM.
def build_index(repo_path: str) -> CodeIndex:
    repo_root = Path(repo_path).resolve()
    files = []
    for path, relative_path in _repo_files(repo_root):
        language = language_of(relative_path)
        indexed = _index_file(path, relative_path, language) if language else None
        if indexed is not None:
            files.append(indexed)
    modules = [indexed.module for indexed in files if indexed.module is not None]
    return CodeIndex(files, resolve_repository(modules))


def _head_sha(repo_path: str) -> str:
    import git

    return git.Repo(repo_path).head.commit.hexsha


# Full index of one checkout into Postgres: python -m rag.code_indexer --repo owner/name --repo-path ./target_repo
def main() -> None:
    import psycopg

    from db.postgres import database_url
    from rag.index_writer import write_index

    parser = argparse.ArgumentParser(description="Index a repository checkout into the Postgres code index.")
    parser.add_argument("--repo", required=True, help="GitHub full name, e.g. owner/name")
    parser.add_argument("--repo-path", default=os.environ.get("REPO_PATH", "."), help="local checkout (default: REPO_PATH or .)")
    parser.add_argument("--sha", help="commit being indexed (default: the checkout's HEAD)")
    arguments = parser.parse_args()

    index = build_index(arguments.repo_path)
    with psycopg.connect(database_url()) as connection:
        stats = write_index(connection, arguments.repo, arguments.sha or _head_sha(arguments.repo_path), index)
    print(f"Indexed {arguments.repo}: {stats.files} files, {stats.symbols} symbols, "
          f"{stats.edges} edges, {stats.unresolved} unresolved calls, {stats.parse_errors} files not parsed.")


if __name__ == "__main__":
    main()
