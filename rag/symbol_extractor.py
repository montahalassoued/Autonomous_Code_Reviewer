import hashlib
from dataclasses import dataclass, field

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser


PY_LANGUAGE = Language(tspython.language())
MODULE_SYMBOL = "<module>"
COLLAPSED_BODY = b"..."

# Calls inside these belong to the inner scope, not to the code around it.
NESTED_SCOPE_TYPES = {"function_definition", "class_definition", "decorated_definition"}

# Statements that bind a local name, and the field holding the target.
LOCAL_BINDING_FIELDS = {
    "assignment": "left", "augmented_assignment": "left", "for_statement": "left", "for_in_clause": "left", "named_expression": "name",
}


# A call as written: foo() has no receiver, self.save() has receiver "self".
@dataclass(frozen=True)
class CallSite:
    receiver: str | None
    name: str

    @property
    def text(self) -> str:
        return f"{self.receiver}.{self.name}" if self.receiver else self.name


# A name an import binds in a file: `from a.b import c as d` binds d to module a.b, attribute c.
@dataclass(frozen=True)
class ImportedName:
    local_name: str
    module: str
    name: str | None = None  # None for `import a.b`, which binds a module


# One row of the symbols table, before it has an id.
@dataclass
class ParsedSymbol:
    name: str
    qualified_name: str
    kind: str  # function | method | class_header | module
    parent: str | None  # qualified_name of the enclosing class, from the tree walk
    signature: str | None
    start_line: int
    end_line: int
    code: str
    calls: list[CallSite] = field(default_factory=list)
    bases: list[CallSite] = field(default_factory=list)  # class_header only: Base or mod.Base
    has_opaque_base: bool = False  # a base we can't follow, e.g. Generic[T]

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.code.encode("utf-8")).hexdigest()


# One Python file: its dotted module name, its symbols (<module> first, parents before children) and imports.
@dataclass
class ParsedModule:
    path: str
    module_name: str
    symbols: list[ParsedSymbol]
    imports: list[ImportedName]
    parse_error: str | None = None


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8")


# "app/api/webhook.py" -> "app.api.webhook"; a package's __init__.py is the package itself.
def module_name_of(path: str) -> str:
    parts = path[:-3].split("/") if path.endswith(".py") else path.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_package(path: str) -> bool:
    return path.endswith("/__init__.py") or path == "__init__.py"


# The call's target as a CallSite, or None when the receiver is not a plain name (a().b(), self.db.find()).
def _call_site(function_part: Node, source: bytes) -> CallSite | None:
    if function_part.type == "identifier":
        return CallSite(None, _text(function_part, source))
    if function_part.type == "attribute":
        receiver = function_part.child_by_field_name("object")
        attribute = function_part.child_by_field_name("attribute")
        if receiver is not None and attribute is not None and receiver.type == "identifier":
            return CallSite(_text(receiver, source), _text(attribute, source))
    return None


# Calls made directly in this scope, in order, without duplicates.
def _extract_calls(scope: Node, source: bytes) -> list[CallSite]:
    calls: list[CallSite] = []

    def walk(node: Node) -> None:
        if node.type == "call":
            function_part = node.child_by_field_name("function")
            site = _call_site(function_part, source) if function_part is not None else None
            if site is not None and site not in calls:
                calls.append(site)
        for child in node.children:
            if child.type not in NESTED_SCOPE_TYPES:
                walk(child)

    for child in scope.children:
        if child.type not in NESTED_SCOPE_TYPES:
            walk(child)
    return calls


# Source with every function and class body under `node` replaced by "...", so headers stay readable.
def _collapsed_code(span: Node, node: Node, source: bytes) -> str:
    cuts: list[tuple[int, int]] = []

    def collect(current: Node) -> None:
        for child in current.children:
            definition = child.child_by_field_name("definition") if child.type == "decorated_definition" else child
            if definition is None:
                continue
            if definition.type in ("function_definition", "class_definition") and definition is not node:
                body = definition.child_by_field_name("body")
                if body is not None:
                    cuts.append((body.start_byte, body.end_byte))
                continue
            collect(child)

    if node.type == "class_definition":
        body = node.child_by_field_name("body")
        if body is not None:
            collect(body)
    else:
        collect(node)

    pieces, position = [], span.start_byte
    for start, end in sorted(cuts):
        pieces.append(source[position:start])
        pieces.append(COLLAPSED_BODY)
        position = end
    pieces.append(source[position:span.end_byte])
    return b"".join(pieces).decode("utf-8")


# Parameters, assigned names and nested def/class names: calls to them (or through them) are local, never repo symbols.
def _local_names(function_node: Node, source: bytes) -> set[str]:
    names = _parameter_names(function_node, source)
    stack = list(function_node.child_by_field_name("body").children)
    while stack:
        node = stack.pop()
        if node.type in ("function_definition", "class_definition"):
            names.add(_text(node.child_by_field_name("name"), source))
        if node.type == "lambda":
            names |= _parameter_names(node, source)
        target = node.child_by_field_name(LOCAL_BINDING_FIELDS[node.type]) if node.type in LOCAL_BINDING_FIELDS else None
        if node.type == "as_pattern":
            target = next((child for child in node.named_children if child.type == "as_pattern_target"), None)
        if target is not None:
            names.update(_text(leaf, source) for leaf in _identifiers(target))
        stack.extend(node.children)
    return names


# Names of a def's or lambda's parameters: x, x=1, x: int, *args, **kwargs.
def _parameter_names(node: Node, source: bytes) -> set[str]:
    names: set[str] = set()
    parameters = node.child_by_field_name("parameters")
    for parameter in parameters.named_children if parameters else []:
        target = parameter.child_by_field_name("name") if parameter.type in ("default_parameter", "typed_default_parameter") else parameter
        if target is not None and target.type in ("typed_parameter", "list_splat_pattern", "dictionary_splat_pattern"):
            target = next((child for child in target.named_children if child.type == "identifier"), None)
        if target is not None and target.type == "identifier":
            names.add(_text(target, source))
    return names


# Calls of a function, including those of its nested defs (inner helpers, decorator wrappers), minus local names.
def _function_calls(function_node: Node, source: bytes, outer_local: frozenset = frozenset()) -> list[CallSite]:
    local = (_local_names(function_node, source) | outer_local) - {"self", "cls"}
    body = function_node.child_by_field_name("body")
    calls = [site for site in _extract_calls(body, source)
             if (site.receiver or site.name) not in local and not (site.receiver is None and site.name in ("self", "cls"))]
    stack = list(body.children)
    while stack:
        node = stack.pop(0)
        if node.type == "function_definition":
            calls += [site for site in _function_calls(node, source, frozenset(local)) if site not in calls]
        elif node.type != "class_definition":
            stack.extend(node.children)
    return calls


# The plain names in an assignment target: x, (a, b), [a, *rest]; not obj.attr or d[key].
def _identifiers(target: Node):
    if target.type == "identifier":
        yield target
    elif target.type in ("pattern_list", "tuple_pattern", "list_pattern", "list_splat_pattern", "as_pattern_target"):
        for child in target.named_children:
            yield from _identifiers(child)


def _signature(function_node: Node, source: bytes) -> str:
    body = function_node.child_by_field_name("body")
    end = body.start_byte if body is not None else function_node.end_byte
    return source[function_node.start_byte:end].decode("utf-8").strip().rstrip(":").strip()


def _resolve_relative(prefix_dots: int, dotted: str, path: str) -> str:
    package = module_name_of(path).split(".") if module_name_of(path) else []
    if not _is_package(path):
        package = package[:-1]
    if prefix_dots - 1 > len(package):
        return ""  # more dots than packages: no module of this repo, so treated as external
    if prefix_dots > 1:
        package = package[: len(package) - (prefix_dots - 1)]
    return ".".join(part for part in package + ([dotted] if dotted else []) if part)


def _module_of_from_import(module_node: Node, source: bytes, path: str) -> str:
    if module_node.type != "relative_import":
        return _text(module_node, source)
    dots, dotted = 0, ""
    for child in module_node.children:
        if child.type == "import_prefix":
            dots = _text(child, source).count(".")
        elif child.type == "dotted_name":
            dotted = _text(child, source)
    return _resolve_relative(dots, dotted, path)


# Every name bound by an import statement anywhere in the file; wildcard imports bind nothing we can follow.
def _extract_imports(root: Node, source: bytes, path: str) -> list[ImportedName]:
    imports: list[ImportedName] = []

    def names_of(statement: Node):
        for index, child in enumerate(statement.children):
            if statement.field_name_for_child(index) != "name":
                continue
            if child.type == "aliased_import":
                yield _text(child.child_by_field_name("name"), source), _text(child.child_by_field_name("alias"), source)
            else:
                yield _text(child, source), None

    def walk(node: Node) -> None:
        if node.type == "import_statement":
            for dotted, alias in names_of(node):
                # `import a.b` binds "a" to module a; `import a.b as m` binds "m" to module a.b
                imports.append(ImportedName(alias, dotted) if alias else ImportedName(dotted.split(".")[0], dotted.split(".")[0]))
        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            if module_node is not None:
                module = _module_of_from_import(module_node, source, path)
                for name, alias in names_of(node):
                    imports.append(ImportedName(alias or name, module, name))
        for child in node.children:
            walk(child)

    walk(root)
    return imports


def _unique(qualified_name: str, seen: dict[str, int]) -> str:
    seen[qualified_name] = seen.get(qualified_name, 0) + 1
    count = seen[qualified_name]
    return qualified_name if count == 1 else f"{qualified_name}#{count}"


# Parses one Python file into its <module> symbol, class headers, functions and methods.
def extract_module(path: str, source_code: str) -> ParsedModule:
    source = source_code.encode("utf-8")
    tree = Parser(PY_LANGUAGE).parse(source)
    root = tree.root_node
    module = ParsedModule(path, module_name_of(path), [], _extract_imports(root, source, path))
    if root.has_error:
        module.parse_error = f"syntax error near line {_first_error_line(root)}"
        return module

    last_line = max(1, source_code.count("\n") + (0 if source_code.endswith("\n") or not source_code else 1))
    module.symbols.append(ParsedSymbol(
        name=MODULE_SYMBOL, qualified_name=MODULE_SYMBOL, kind="module", parent=None, signature=None,
        start_line=1, end_line=last_line, code=_collapsed_code(root, root, source), calls=_extract_calls(root, source),
    ))
    seen: dict[str, int] = {}

    def add_function(function_node: Node, span: Node, parent: ParsedSymbol | None) -> None:
        name = _text(function_node.child_by_field_name("name"), source)
        prefix = f"{parent.qualified_name.split('#')[0]}." if parent else ""
        calls = _function_calls(function_node, source)
        module.symbols.append(ParsedSymbol(
            name=name, qualified_name=_unique(prefix + name, seen),
            kind="method" if parent else "function", parent=parent.qualified_name if parent else None,
            signature=_signature(function_node, source), start_line=span.start_point[0] + 1,
            end_line=span.end_point[0] + 1, code=_text(span, source), calls=calls,
        ))

    def add_class(class_node: Node, span: Node, parent: ParsedSymbol | None) -> None:
        name = _text(class_node.child_by_field_name("name"), source)
        prefix = f"{parent.qualified_name.split('#')[0]}." if parent else ""
        superclasses = class_node.child_by_field_name("superclasses")
        base_nodes = [child for child in (superclasses.named_children if superclasses else []) if child.type != "keyword_argument"]
        bases = [site for child in base_nodes if (site := _call_site(child, source))]
        header = ParsedSymbol(
            name=name, qualified_name=_unique(prefix + name, seen), kind="class_header",
            parent=parent.qualified_name if parent else None, signature=None,
            start_line=span.start_point[0] + 1, end_line=span.end_point[0] + 1,
            code=_collapsed_code(span, class_node, source), calls=_extract_calls(class_node.child_by_field_name("body"), source),
            bases=bases, has_opaque_base=len(bases) < len(base_nodes),
        )
        module.symbols.append(header)
        walk(class_node.child_by_field_name("body"), header)

    def walk(node: Node, enclosing_class: ParsedSymbol | None) -> None:
        for child in node.children:
            span, definition = child, child
            if child.type == "decorated_definition":
                definition = child.child_by_field_name("definition")
            if definition is None:
                continue
            if definition.type == "function_definition":
                add_function(definition, span, enclosing_class)  # nested defs stay inside their function
            elif definition.type == "class_definition":
                add_class(definition, span, enclosing_class)
            else:
                walk(child, enclosing_class)  # if / try / with blocks at module or class level

    walk(root, None)
    return module


def _first_error_line(root: Node) -> int:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            return node.start_point[0] + 1
        stack.extend(reversed(node.children))
    return 1
