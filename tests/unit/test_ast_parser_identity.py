from rag.ast_parser import parse_file
from rag.contextual_chunker import ContextualChunk
from rag.knowledge_graph import build_knowledge_graph
from db.chroma import make_chunk_id


FILE_PATH = "app/storage.py"

SAMPLE_CODE = '''def retry(times):
    def decorator(fn):
        return fn
    return decorator


class Repository:
    def __init__(self, db):
        self.db = db

    @retry(3)
    def save(self, item):
        validate(item)
        return self.db.insert(item)


class Cache:
    def __init__(self):
        self.store = {}

    class Entry:
        def expired(self):
            return check_clock()


def outer(values):
    def inner(value):
        return transform(value)
    return [inner(v) for v in values]
'''

PROPERTY_CODE = '''class Account:
    @property
    def balance(self):
        return self._balance

    @balance.setter
    def balance(self, value):
        self._balance = value
'''


def parse_sample() -> dict:
    """Parses SAMPLE_CODE and returns its functions keyed by qualified_name."""
    return {fn.qualified_name: fn for fn in parse_file(FILE_PATH, SAMPLE_CODE)}


def test_methods_get_class_qualified_names():
    functions = parse_sample()

    assert set(functions) == {
        "retry",
        "Repository.__init__",
        "Repository.save",
        "Cache.__init__",
        "Cache.Entry.expired",
        "outer",
    }, f"Unexpected qualified names: {sorted(functions)}"
    # the bare name is kept for call resolution in the knowledge graph
    assert functions["Repository.save"].name == "save"
    assert functions["Cache.Entry.expired"].name == "expired"
    assert functions["outer"].name == "outer"
    print(f"OK - qualified names: {sorted(functions)}")


def test_decorators_are_included_in_code_and_start_line():
    save = parse_sample()["Repository.save"]
    decorator_line = SAMPLE_CODE.splitlines().index("    @retry(3)") + 1

    assert save.code.startswith("@retry(3)"), f"Decorator missing from code: {save.code!r}"
    assert "def save(self, item):" in save.code
    assert save.start_line == decorator_line, (
        f"start_line should point at the decorator (line {decorator_line}), got {save.start_line}"
    )
    # the decorator call runs at import time, so it isn't one of save()'s calls
    assert "retry" not in save.calls
    assert "validate" in save.calls
    print("OK - decorator included in code and start_line, not in calls")


def test_nested_def_is_not_a_separate_chunk():
    functions = parse_sample()
    bare_names = {fn.name for fn in functions.values()}

    assert "inner" not in bare_names, "nested def inner() must not be its own chunk"
    assert "decorator" not in bare_names, "nested def decorator() must not be its own chunk"
    assert "def inner(value):" in functions["outer"].code, "nested def stays inside the parent's code"
    print("OK - nested defs stay inside their parent's chunk only")


def test_nested_def_calls_are_not_attributed_to_parent():
    outer = parse_sample()["outer"]

    assert "transform" not in outer.calls, (
        "transform() is called inside inner(), not by outer() itself"
    )
    assert "inner" in outer.calls, "outer() does call inner() directly"
    print(f"OK - outer() calls: {outer.calls}")


def test_two_init_methods_get_distinct_ids():
    functions = parse_file(FILE_PATH, SAMPLE_CODE)
    graph = build_knowledge_graph(functions)

    assert f"{FILE_PATH}::Repository.__init__" in graph
    assert f"{FILE_PATH}::Cache.__init__" in graph
    assert graph.number_of_nodes() == len(functions), "every function needs its own graph node"

    chunk_ids = [make_chunk_id(ContextualChunk(function=fn, context="")) for fn in functions]
    assert len(chunk_ids) == len(set(chunk_ids)), f"Duplicate chunk ids: {chunk_ids}"
    print(f"OK - {len(chunk_ids)} unique chunk ids, both __init__ methods distinct")


def test_same_qualified_name_twice_gets_a_suffix():
    functions = parse_file("app/account.py", PROPERTY_CODE)
    qualified_names = [fn.qualified_name for fn in functions]

    # a @property and its setter share a name; Chroma rejects duplicate
    # ids in one upsert, so the second one is suffixed
    assert qualified_names == ["Account.balance", "Account.balance#2"], qualified_names
    assert all(fn.code.startswith("@") for fn in functions), "both keep their decorators"
    print(f"OK - duplicate names disambiguated: {qualified_names}")


if __name__ == "__main__":
    test_methods_get_class_qualified_names()
    test_decorators_are_included_in_code_and_start_line()
    test_nested_def_is_not_a_separate_chunk()
    test_nested_def_calls_are_not_attributed_to_parent()
    test_two_init_methods_get_distinct_ids()
    test_same_qualified_name_twice_gets_a_suffix()
    print("\nAll ast_parser identity tests passed.")
