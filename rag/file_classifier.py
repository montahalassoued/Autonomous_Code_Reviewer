from fnmatch import fnmatchcase


SKIPPED_DIRS = {
    ".git", "venv", ".venv", "env", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".tox", "site-packages", "dist", "build",
}
MAX_TEXT_FILE_BYTES = 100_000

# Never stored or shown to a model: their content can end up in a public PR comment. Matched in lowercase.
SECRET_FILE_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", ".netrc", ".pypirc", ".npmrc",
    ".git-credentials", "*.tfvars", "*.tfstate", "*.tfstate.*",
)
# Data files whose name says they hold secrets; code such as secrets.py stays reviewable.
SECRET_DATA_PATTERNS = ("*credential*", "*secret*", "service-account*")
DATA_SUFFIXES = (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".txt", ".xml", ".properties")

# Non-Python files kept as plain text so config, docs and manifests can be grepped.
TEXT_LANGUAGES = {
    ".toml": "toml", ".cfg": "ini", ".ini": "ini", ".yaml": "yaml", ".yml": "yaml", ".json": "json",
    ".md": "markdown", ".txt": "text", ".js": "javascript", ".ts": "typescript", ".sql": "sql", ".sh": "shell",
}
TEXT_FILE_NAMES = {"Dockerfile": "dockerfile", "Makefile": "makefile"}


def _file_name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def is_secret_file(path: str) -> bool:
    name = _file_name(path).lower()
    if any(fnmatchcase(name, pattern) for pattern in SECRET_FILE_PATTERNS):
        return True
    return name.endswith(DATA_SUFFIXES) and any(fnmatchcase(name, pattern) for pattern in SECRET_DATA_PATTERNS)


def is_test_file(path: str) -> bool:
    name = _file_name(path)
    return path.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{path}")


# "python", a text language, or None when the file is not indexed.
def language_of(path: str) -> str | None:
    if is_secret_file(path):
        return None
    if path.endswith(".py"):
        return "python"
    name = _file_name(path)
    if name in TEXT_FILE_NAMES:
        return TEXT_FILE_NAMES[name]
    suffix = name[name.rfind("."):] if "." in name else ""
    return TEXT_LANGUAGES.get(suffix)
