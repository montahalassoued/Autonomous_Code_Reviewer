import builtins
from dataclasses import dataclass, field

from code_index.file_classifier import is_test_file
from code_index.symbol_extractor import MODULE_SYMBOL, CallSite, ImportedName, ParsedModule, ParsedSymbol


BUILTIN_NAMES = frozenset(dir(builtins))
MAX_IMPORT_HOPS = 3  # re-exports followed through __init__.py files
SRC_LAYOUT_ROOT = "src"

# A symbol is identified by (file path, qualified_name) until the writer gives it an id.
SymbolKey = tuple[str, str]


@dataclass(frozen=True)
class Edge:
    src: SymbolKey
    kind: str  # calls | imports | inherits | tested_by
    dst: SymbolKey


@dataclass(frozen=True)
class UnresolvedCall:
    src: SymbolKey
    name: str
    reason: str  # not_found | ambiguous | dynamic


@dataclass
class Resolution:
    edges: list[Edge] = field(default_factory=list)
    unresolved: list[UnresolvedCall] = field(default_factory=list)


# Found one symbol, found several (ambiguous), found none in the repo, or the target is outside the repo.
@dataclass(frozen=True)
class _Lookup:
    keys: tuple[SymbolKey, ...] = ()
    external: bool = False


EXTERNAL = _Lookup(external=True)
NOT_FOUND = _Lookup()


# Lookups over every parsed module; resolution never guesses between candidates.
class _RepoIndex:

    def __init__(self, modules: list[ParsedModule]) -> None:
        self.modules = {module.module_name: module for module in modules if module.parse_error is None}
        self.by_path = {module.path: module for module in self.modules.values()}
        self.top_level: dict[str, dict[str, list[SymbolKey]]] = {}
        self.methods: dict[SymbolKey, dict[str, list[SymbolKey]]] = {}
        self.by_name: dict[str, list[SymbolKey]] = {}
        self.symbols: dict[SymbolKey, ParsedSymbol] = {}
        for module in self.modules.values():
            names = self.top_level.setdefault(module.path, {})
            for symbol in module.symbols:
                key = (module.path, symbol.qualified_name)
                self.symbols[key] = symbol
                if symbol.kind == "method" and symbol.parent:
                    self.methods.setdefault((module.path, symbol.parent), {}).setdefault(symbol.name, []).append(key)
                elif symbol.kind in ("function", "class_header") and symbol.parent is None:
                    names.setdefault(symbol.name, []).append(key)
                    self.by_name.setdefault(symbol.name, []).append(key)

    # Exact module name, or the same name under src/ (a src layout imports "pkg.mod" from src/pkg/mod.py).
    def find_module(self, dotted: str) -> ParsedModule | None:
        return self.modules.get(dotted) or self.modules.get(f"{SRC_LAYOUT_ROOT}.{dotted}")

    def module_symbol(self, module: ParsedModule) -> SymbolKey:
        return (module.path, MODULE_SYMBOL)

    # `name` defined in a module, or re-exported by one of its imports (`from .service import login` in __init__.py).
    def name_in_module(self, module: ParsedModule, name: str, hops: int = 0) -> _Lookup:
        keys = self.top_level.get(module.path, {}).get(name, [])
        if keys:
            return _Lookup(tuple(keys))
        for imported in module.imports:
            if imported.local_name == name and hops < MAX_IMPORT_HOPS:
                return self.follow_import(imported, hops + 1)
        return NOT_FOUND

    # What an import binds: a symbol of a repo module, a repo module itself, or something external.
    def follow_import(self, imported: ImportedName, hops: int = 0) -> _Lookup:
        if imported.name is None:
            target = self.find_module(imported.module)
            return _Lookup((self.module_symbol(target),)) if target else EXTERNAL
        submodule = self.find_module(f"{imported.module}.{imported.name}")
        if submodule is not None:
            return _Lookup((self.module_symbol(submodule),))
        target = self.find_module(imported.module)
        if target is None:
            return EXTERNAL
        return self.name_in_module(target, imported.name, hops)

    # A method on a class or, failing that, on its bases inside the repo.
    def method_of(self, class_key: SymbolKey, name: str, seen: frozenset = frozenset()) -> _Lookup:
        if class_key in seen:
            return NOT_FOUND
        keys = self.methods.get(class_key, {}).get(name, [])
        if keys:
            return _Lookup(tuple(keys))
        has_external_base = self.symbols[class_key].has_opaque_base
        for base in self.bases_of(class_key):
            if base.external or len(base.keys) != 1:
                has_external_base = True  # the method may come from code we never parsed
                continue
            found = self.method_of(base.keys[0], name, seen | {class_key})
            if found.keys:
                return found
            has_external_base = has_external_base or found.external
        return EXTERNAL if has_external_base else NOT_FOUND

    def bases_of(self, class_key: SymbolKey) -> list[_Lookup]:
        module = self.module_at(class_key[0])
        symbol = self.symbols[class_key]
        return [self.resolve_name(module, base, symbol) for base in symbol.bases]

    def module_at(self, path: str) -> ParsedModule:
        return self.by_path[path]

    def is_class(self, lookup: _Lookup) -> bool:
        return len(lookup.keys) == 1 and self.symbols[lookup.keys[0]].kind == "class_header"

    def is_module(self, lookup: _Lookup) -> bool:
        return len(lookup.keys) == 1 and lookup.keys[0][1] == MODULE_SYMBOL

    # self/cls method → imported name → same file → unique name in the repo → not found; builtins are external.
    def resolve_name(self, module: ParsedModule, site: CallSite, caller: ParsedSymbol) -> _Lookup:
        if site.receiver in ("self", "cls"):
            if caller.kind != "method" or caller.parent is None:
                return EXTERNAL
            return self.method_of((module.path, caller.parent), site.name)
        if site.receiver is None:
            return self.resolve_bare(module, site.name)
        receiver = self.resolve_bare(module, site.receiver)
        if self.is_module(receiver):
            return self.name_in_module(self.module_at(receiver.keys[0][0]), site.name)
        if self.is_class(receiver):
            return self.method_of(receiver.keys[0], site.name)
        return EXTERNAL  # a local variable or an object whose type static analysis can't know

    def resolve_bare(self, module: ParsedModule, name: str) -> _Lookup:
        for imported in reversed(module.imports):
            if imported.local_name == name:
                return self.follow_import(imported)
        same_file = self.top_level.get(module.path, {}).get(name, [])
        if same_file:
            return _Lookup(tuple(same_file))
        if name in BUILTIN_NAMES:
            return EXTERNAL
        return _Lookup(tuple(self.by_name.get(name, [])))


def _add_lookup(resolution: Resolution, src: SymbolKey, kind: str, lookup: _Lookup, written: str) -> None:
    if lookup.external:
        return
    if len(lookup.keys) == 1:
        if lookup.keys[0] != src:
            resolution.edges.append(Edge(src, kind, lookup.keys[0]))
    elif kind == "calls":
        reason = "ambiguous" if lookup.keys else "not_found"
        resolution.unresolved.append(UnresolvedCall(src, written, reason))


# Calls, imports, inherits and tested_by edges between the parsed modules, plus the calls that could not be linked.
def resolve_repository(modules: list[ParsedModule]) -> Resolution:
    index = _RepoIndex(modules)
    resolution = Resolution()
    for module in index.modules.values():
        for symbol in module.symbols:
            src = (module.path, symbol.qualified_name)
            for site in symbol.calls:
                _add_lookup(resolution, src, "calls", index.resolve_name(module, site, symbol), site.text)
            if symbol.kind == "class_header":
                for base_site, base in zip(symbol.bases, index.bases_of(src)):
                    _add_lookup(resolution, src, "inherits", base, base_site.text)
        module_key = index.module_symbol(module)
        for imported in module.imports:
            _add_lookup(resolution, module_key, "imports", index.follow_import(imported), imported.local_name)

    tested = [Edge(edge.dst, "tested_by", edge.src) for edge in resolution.edges
              if edge.kind == "calls" and is_test_file(edge.src[0]) and not is_test_file(edge.dst[0])]
    resolution.edges.extend(tested)
    resolution.edges = list(dict.fromkeys(resolution.edges))
    resolution.unresolved = list({(call.src, call.name): call for call in resolution.unresolved}.values())
    return resolution
