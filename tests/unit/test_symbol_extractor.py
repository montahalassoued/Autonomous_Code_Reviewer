from rag.symbol_extractor import MODULE_SYMBOL, CallSite, ImportedName, extract_module, module_name_of


SERVICE_SOURCE = '''import bcrypt
from .models import User
from ..core import config as settings


class AuthService(Base, metaclass=Meta):
    limit = compute_limit()

    @staticmethod
    def helper(value):
        return value

    async def login(self, user, password) -> bool:
        checker = build_checker()
        if verify_password(password, user.hash) and checker(user):
            self.save()
            return create_session(user)
        return self.db.find(user)


class AuthService:
    pass


def verify_password(password, hashed):
    def walk(node):
        return node
    return bcrypt.checkpw(password, walk(hashed))
'''


def symbols_by_name(source: str = SERVICE_SOURCE, path: str = "app/auth/service.py") -> dict:
    return {symbol.qualified_name: symbol for symbol in extract_module(path, source).symbols}


def test_every_kind_of_symbol_is_extracted_with_its_parent():
    symbols = symbols_by_name()

    assert list(symbols) == [MODULE_SYMBOL, "AuthService", "AuthService.helper", "AuthService.login", "AuthService#2", "verify_password"]
    assert [symbols[name].kind for name in symbols] == ["module", "class_header", "method", "method", "class_header", "function"]
    assert symbols["AuthService.login"].parent == "AuthService"
    assert symbols["AuthService.login"].name == "login", "the bare name is what call sites use"
    assert symbols["AuthService#2"].parent is None
    print("OK - <module>, class headers, methods and functions, duplicates suffixed #2")


def test_signature_lines_and_decorators():
    symbols = symbols_by_name()

    assert symbols["AuthService.login"].signature == "async def login(self, user, password) -> bool"
    helper = symbols["AuthService.helper"]
    assert helper.code.startswith("@staticmethod"), "decorators belong to the symbol"
    assert (helper.start_line, helper.end_line) == (9, 11)
    print("OK - signature, 1-indexed lines and decorators")


def test_class_header_and_module_collapse_bodies():
    symbols = symbols_by_name()

    header = symbols["AuthService"].code
    assert "limit = compute_limit()" in header
    assert "async def login(self, user, password) -> bool:\n        ..." in header
    assert "create_session" not in header, "method bodies are collapsed in the class header"
    assert "def verify_password(password, hashed):\n    ..." in symbols[MODULE_SYMBOL].code
    print("OK - headers keep class attributes and method signatures only")


def test_calls_keep_their_receiver_and_skip_locals():
    symbols = symbols_by_name()

    calls = symbols["AuthService.login"].calls
    assert CallSite(None, "verify_password") in calls
    assert CallSite("self", "save") in calls
    assert CallSite(None, "build_checker") in calls
    assert CallSite(None, "checker") not in calls, "checker is a local variable"
    assert all(site.text != "self.db.find" for site in calls), "chained receivers cannot be resolved statically"
    assert CallSite(None, "walk") not in symbols["verify_password"].calls, "walk is a nested def"
    assert symbols["AuthService"].calls == [CallSite(None, "compute_limit")], "class body calls belong to the header"
    print("OK - calls as written, locals and nested defs dropped")


def test_comprehension_lambda_and_except_names_are_locals_and_nested_calls_count():
    source = """def run(handlers, items):
    try:
        results = [h(item) for h in handlers for item in items]
        pick = lambda k: k()
    except ValueError as error:
        error()

    def inner():
        return audit(results)
    return inner()
"""
    calls = [site.text for site in extract_module("app/run.py", source).symbols[1].calls]

    assert calls == ["audit"], f"only the repo-level call survives: {calls}"
    print("OK - h, k, error and inner are locals; the nested helper's audit() call is kept")


def test_relative_import_with_too_many_dots_is_external():
    imports = extract_module("app.py", "from ...pkg import x\n").imports

    assert imports == [ImportedName("x", "", "x")]
    print("OK - more dots than packages never resolves to a repo module")


def test_bases_skip_keyword_arguments():
    header = symbols_by_name()["AuthService"]

    assert header.bases == [CallSite(None, "Base")]
    assert header.has_opaque_base is False
    assert extract_module("a.py", "class Repo(Generic[T]):\n    pass\n").symbols[1].has_opaque_base is True
    print("OK - metaclass= is not a base, Generic[T] is an opaque base")


def test_imports_resolve_relative_modules_and_aliases():
    imports = extract_module("app/auth/service.py", SERVICE_SOURCE).imports

    assert imports == [
        ImportedName("bcrypt", "bcrypt"),
        ImportedName("User", "app.auth.models", "User"),
        ImportedName("settings", "app.core", "config"),
    ]
    print("OK - relative imports become absolute module names")


def test_module_names_and_empty_and_broken_files():
    assert module_name_of("app/api/webhook.py") == "app.api.webhook"
    assert module_name_of("app/api/__init__.py") == "app.api"

    empty = extract_module("app/__init__.py", "")
    assert [(symbol.kind, symbol.start_line, symbol.end_line) for symbol in empty.symbols] == [("module", 1, 1)]

    broken = extract_module("bad.py", "def f(:\n    pass\n")
    assert broken.symbols == [] and broken.parse_error.startswith("syntax error near line 1")
    print("OK - every file has <module>; a syntax error is recorded, not raised")
