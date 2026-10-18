from rag.call_resolver import Edge, UnresolvedCall, resolve_repository
from rag.symbol_extractor import extract_module


MODELS = '''class Base:
    def save(self):
        return persist(self)


class User(Base):
    @classmethod
    def find(cls, name):
        return cls.load(name)


def persist(item):
    return len(item)
'''

SERVICE = '''import json
from app.models import User
from app import models
from app.models import format_name


def login(name):
    user = User.find(name)
    user.save()
    models.persist(user)
    send_email(name)
    format_name(name)
    return json.dumps(make_token(user))


def make_token(user):
    return str(user)


class Session(User):
    def close(self):
        self.save()
        self.flush()
'''

PACKAGE_INIT = "from app.models import persist\n"

DUPLICATES = '''def send_email(name):
    return name
'''

OTHER_DUPLICATE = '''def send_email(name):
    return None
'''

TESTS = '''from app.service import login


def test_login():
    assert login("ada")
'''


def resolve(extra: dict | None = None):
    files = {
        "app/models.py": MODELS, "app/service.py": SERVICE, "app/__init__.py": PACKAGE_INIT,
        "app/mail.py": DUPLICATES, "legacy/mail.py": OTHER_DUPLICATE, "tests/test_service.py": TESTS,
    }
    files.update(extra or {})
    return resolve_repository([extract_module(path, source) for path, source in files.items()])


def edges_of(resolution, kind: str) -> set[tuple]:
    return {(edge.src, edge.dst) for edge in resolution.edges if edge.kind == kind}


def test_calls_resolve_through_imports_same_file_and_classes():
    calls = edges_of(resolve(), "calls")

    login = ("app/service.py", "login")
    assert (login, ("app/models.py", "User.find")) in calls, "Imported.method()"
    assert (login, ("app/models.py", "persist")) in calls, "module_alias.function()"
    assert (login, ("app/service.py", "make_token")) in calls, "same file"
    assert (("app/models.py", "Base.save"), ("app/models.py", "persist")) in calls
    print("OK - imported class method, module alias, same-file calls")


def test_self_and_cls_calls_follow_base_classes():
    resolution = resolve()
    calls = edges_of(resolution, "calls")

    assert (("app/service.py", "Session.close"), ("app/models.py", "Base.save")) in calls, "self.save() found on a base class"
    assert UnresolvedCall(("app/service.py", "Session.close"), "self.flush", "not_found") in resolution.unresolved
    assert UnresolvedCall(("app/models.py", "User.find"), "cls.load", "not_found") in resolution.unresolved
    print("OK - self/cls methods resolve through repo bases, missing ones are recorded")


def test_external_calls_and_locals_are_not_stored():
    resolution = resolve()
    written = {(call.src, call.name) for call in resolution.unresolved}
    targets = {edge.dst for edge in resolution.edges}

    assert not any(name in ("json.dumps", "str", "len", "user.save") for _src, name in written)
    assert not any(path.startswith("json") for path, _name in targets)
    print("OK - library, builtin and local-variable calls are neither edges nor unresolved")


def test_ambiguous_and_missing_names_are_never_guessed():
    resolution = resolve()
    login = ("app/service.py", "login")

    assert UnresolvedCall(login, "send_email", "ambiguous") in resolution.unresolved, "defined in two files"
    assert UnresolvedCall(login, "format_name", "not_found") in resolution.unresolved, "imported from a repo module that lacks it"
    assert not any(edge.src == login and edge.dst[1] == "send_email" for edge in resolution.edges)
    print("OK - two candidates = ambiguous, none = not_found, no edge in either case")


def test_imports_inherits_and_tested_by_edges():
    resolution = resolve()

    assert (("app/service.py", "<module>"), ("app/models.py", "User")) in edges_of(resolution, "imports")
    assert (("app/service.py", "<module>"), ("app/models.py", "<module>")) in edges_of(resolution, "imports"), "from app import models"
    assert (("app/models.py", "User"), ("app/models.py", "Base")) in edges_of(resolution, "inherits")
    assert (("app/service.py", "Session"), ("app/models.py", "User")) in edges_of(resolution, "inherits")
    assert Edge(("app/service.py", "login"), "tested_by", ("tests/test_service.py", "test_login")) in resolution.edges
    print("OK - <module> imports, class inheritance, code -> test edges")


def test_reexport_through_package_init_is_followed():
    resolution = resolve({"app/cli.py": "from app import persist\n\ndef run(x):\n    return persist(x)\n"})

    assert (("app/cli.py", "run"), ("app/models.py", "persist")) in edges_of(resolution, "calls")
    print("OK - `from app import persist` follows the re-export in app/__init__.py")


def test_src_layout_and_external_bases():
    resolution = resolve({
        "src/shop/cart.py": "class Cart(Model):\n    def total(self):\n        return self.sum()\n",
        "src/shop/api.py": "from shop.cart import Cart\n\ndef show():\n    return Cart()\n",
    })

    assert (("src/shop/api.py", "show"), ("src/shop/cart.py", "Cart")) in edges_of(resolution, "calls"), "src layout"
    assert not any(call.name == "self.sum" for call in resolution.unresolved), "Model is external, sum may come from it"
    print("OK - src/ layout imports resolve, methods of external bases are not flagged")


def test_broken_file_gets_no_symbols_or_edges():
    resolution = resolve({"app/broken.py": "def f(:\n"})

    assert not any("app/broken.py" in (edge.src[0], edge.dst[0]) for edge in resolution.edges)
    print("OK - a file with a syntax error is skipped by resolution")
