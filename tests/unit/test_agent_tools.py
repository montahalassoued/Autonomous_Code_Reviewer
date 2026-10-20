import json
from types import SimpleNamespace

import pytest

from rag.ast_parser import parse_file
from rag.knowledge_graph import build_knowledge_graph
from review.agents.agent_runner import FINAL_ANSWER_REQUEST, run_agent
from review.agents.logic.agent import LOGIC_AGENT
from review.agents.logic.tools.find_exception_handlers import find_exception_handlers
from review.agents.quality.agent import QUALITY_AGENT
from review.agents.quality.tools.find_similar_functions import find_similar_functions
from review.agents.security.agent import SECURITY_AGENT
from review.agents.security.tools.find_auth_guards import find_auth_guards
from review.agents.security.tools.find_input_guards import find_input_guards
from review.agents.security.tools.find_security_settings import find_security_settings
from review.agents.security.tools.find_sql_queries import find_sql_queries
from review.agents.tools.code_graph import find_callees, find_callers
from review.agents.tools.related_tests import find_tests
from review.agents.tools.repo_files import read_file, resolve_inside_repo, search_code
from review.agents.tools.tool_definition import MAX_TOOL_OUTPUT_CHARS, Tool, ToolContext, execute_tool_call


AUTH_SOURCE = """from fastapi import APIRouter, Depends
from app.auth import require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def login(user, password):
    return verify_password(password, user.hash)


def verify_password(password, hashed):
    return hashed == password
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "admin.py").write_text(AUTH_SOURCE, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_admin.py").write_text("def test_login():\n    assert login(u, 'p')\n", encoding="utf-8")
    (tmp_path / "venv").mkdir()
    (tmp_path / "venv" / "skip.py").write_text("Depends(should_not_be_found)\n", encoding="utf-8")
    return ToolContext(repo_root=tmp_path)


def test_read_file_numbers_lines_and_respects_the_range(repo):
    output = read_file(repo, {"path": "app/admin.py", "start_line": 7, "end_line": 8})

    assert output == "7: def login(user, password):\n8:     return verify_password(password, user.hash)"
    print("OK - read_file returns numbered lines for the requested range")


def test_paths_outside_the_repo_are_refused(repo):
    for path in ("../secret.txt", "/etc/passwd", "app/../../x.py"):
        with pytest.raises(ValueError, match="outside the repository"):
            resolve_inside_repo(repo, path)
    print("OK - ../ and absolute paths cannot escape the checkout")


def test_secret_files_are_never_read_or_searched(repo):
    root = repo.repo_root
    (root / ".env").write_text("GROQ_API_KEY=sk-live-123\n", encoding="utf-8")
    (root / "deploy.pem").write_text("-----BEGIN KEY----- sk-live-123\n", encoding="utf-8")

    for path in (".env", "deploy.pem", ".git/config"):
        with pytest.raises(ValueError):
            resolve_inside_repo(repo, path)
    assert "sk-live-123" not in search_code(repo, {"text": "sk-live"})
    print("OK - .env, keys and .git/ are refused by read_file and skipped by search")


def test_symlinks_are_not_followed_by_search(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.txt"
    outside.write_text("TOP-SECRET-VALUE\n", encoding="utf-8")
    link = repo.repo_root / "app" / "notes.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks needs extra rights on this system")

    assert "TOP-SECRET-VALUE" not in search_code(repo, {"text": "TOP-SECRET"})
    with pytest.raises(ValueError, match="outside the repository"):
        resolve_inside_repo(repo, "app/notes.txt")
    print("OK - a committed symlink cannot leak a file from outside the checkout")


def test_search_code_is_literal_and_skips_venv(repo):
    assert "app/admin.py:7: def login(user, password):" in search_code(repo, {"text": "def login"})
    assert "No match" in search_code(repo, {"text": "def lo.in"}), "the text is literal, not a regex"
    assert "should_not_be_found" not in search_code(repo, {"text": "Depends("})
    print("OK - literal search, venv/ ignored")


def test_find_auth_guards_shows_router_level_dependency(repo):
    output = find_auth_guards(repo, {"path_prefix": "app"})

    assert 'app/admin.py:4: router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])' in output
    print("OK - the router-level guard is visible to the security agent")


def test_auth_guard_prefix_matches_whole_folders(repo):
    (repo.repo_root / "appx").mkdir()
    (repo.repo_root / "appx" / "other.py").write_text("x = Depends(get_user)\n", encoding="utf-8")

    output = find_auth_guards(repo, {"path_prefix": "app/"})

    assert "app/admin.py:4:" in output and "appx/" not in output
    print("OK - path_prefix 'app' does not match 'appx'")


def test_find_sql_queries_shows_how_queries_are_built(repo):
    (repo.repo_root / "app" / "orders.py").write_text(
        "def get(cursor, order_id):\n    cursor.execute(f\"SELECT * FROM orders WHERE id = {order_id}\")\n",
        encoding="utf-8",
    )

    output = find_sql_queries(repo, {"path_prefix": "app"})

    assert output == 'app/orders.py:2: cursor.execute(f"SELECT * FROM orders WHERE id = {order_id}")'
    assert "No SQL query found." == find_sql_queries(repo, {"path_prefix": "tests"})
    print("OK - the security agent can follow input into the SQL it reaches")


def test_find_input_guards_skips_tests_and_filters_by_kind(repo):
    (repo.repo_root / "app" / "uploads.py").write_text(
        "from werkzeug.utils import secure_filename\n\n\ndef save(upload):\n"
        "    name = secure_filename(upload.filename)\n    return html.escape(name)\n",
        encoding="utf-8",
    )
    (repo.repo_root / "tests" / "test_uploads.py").write_text("def test_save():\n    assert secure_filename('a')\n", encoding="utf-8")

    path_guards = find_input_guards(repo, {"kind": "path"})
    html_guards = find_input_guards(repo, {"kind": "HTML"})

    assert path_guards == "app/uploads.py:5: name = secure_filename(upload.filename)", "test files are not guards"
    assert html_guards == "app/uploads.py:6: return html.escape(name)"
    assert "Unknown kind 'sql'" in find_input_guards(repo, {"kind": "sql"})
    assert find_input_guards(repo, {"path_prefix": "tests"}) == "No input validation or escaping code found."
    print("OK - the security agent sees production input guards, by sink kind, never test helpers")


def test_find_security_settings_ignores_test_settings_and_docs(repo):
    cors_line = "app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True)"
    (repo.repo_root / "app" / "main.py").write_text(cors_line + "\n", encoding="utf-8")
    (repo.repo_root / "tests" / "settings.py").write_text("WTF_CSRF_ENABLED = False\n", encoding="utf-8")
    (repo.repo_root / "tests" / "config.yaml").write_text("verify=False\n", encoding="utf-8")
    (repo.repo_root / "README.md").write_text("Set DEBUG = True locally\n", encoding="utf-8")

    output = find_security_settings(repo, {})

    assert output == f"app/main.py:1: {cors_line}", "test settings and docs must not look like production config"
    assert find_security_settings(repo, {"path_prefix": "tests"}).startswith("No CORS, CSRF")
    print("OK - the security agent sees app-wide CORS/CSRF/cookie settings, not test overrides")


def test_find_exception_handlers_filters_by_exception(repo):
    (repo.repo_root / "app" / "service.py").write_text(
        "def load(key):\n    raise KeyError(key)\n\n\ndef safe(key):\n    try:\n        return load(key)\n"
        "    except KeyError:\n        return None\n    except ValueError:\n        pass\n",
        encoding="utf-8",
    )

    output = find_exception_handlers(repo, {"exception": "KeyError"})

    assert output.splitlines() == ["app/service.py:2: raise KeyError(key)", "app/service.py:8: except KeyError:"]
    assert "No raise or except of 'OSError'" in find_exception_handlers(repo, {"exception": "OSError"})
    print("OK - the logic agent sees who raises an exception and who catches it")


def test_find_similar_functions_finds_an_existing_helper():
    source = "def parse_date(text):\n    return text\n\n\ndef parse_date_utc(text):\n    return text\n"
    graph = build_knowledge_graph(parse_file("app/dates.py", source) + parse_file("app/new.py", "def parse_dates(t):\n    return t\n"))
    context = ToolContext(code_graph=graph)

    output = find_similar_functions(context, {"name": "parse_date", "exclude_file": "app/new.py"})

    assert "- parse_date (app/dates.py:1)" in output and "- parse_date_utc (app/dates.py:5)" in output
    assert "app/new.py" not in output
    assert "at least" in find_similar_functions(context, {"name": "pd"})
    print("OK - the quality agent can check whether a new helper already exists")


def test_every_agent_has_its_own_tool_and_unique_names():
    for spec, own_tools in (
        (SECURITY_AGENT, ("find_auth_guards", "find_input_guards", "find_security_settings", "find_sql_queries")),
        (LOGIC_AGENT, ("find_return_values", "find_call_sites", "find_exception_handlers")),
        (QUALITY_AGENT, ("find_similar_functions", "find_loop_queries", "find_blocking_calls_in_async")),
    ):
        names = [tool.name for tool in spec.tools]
        for own_tool in own_tools:
            assert own_tool in names, f"{spec.name} is missing {own_tool}"
        assert len(names) == len(set(names)), f"{spec.name} offers a tool twice"
    print("OK - each agent's toolset has its own tools and no duplicate name")


def test_tool_resource_must_be_repo_or_graph():
    with pytest.raises(ValueError, match="requires must be one of"):
        Tool(name="x", description="d", parameters={}, run=lambda context, arguments: "", requires="repos")
    print("OK - a typo in requires fails at import, not silently at review time")


def test_find_tests_only_looks_in_test_files(repo):
    output = find_tests(repo, {"name": "login"})

    assert output.startswith("tests/test_admin.py:")
    assert "app/admin.py" not in output
    print("OK - find_tests searches test files only")


def test_find_callers_and_callees_use_the_call_graph():
    context = ToolContext(code_graph=build_knowledge_graph(parse_file("app/admin.py", AUTH_SOURCE)))

    assert "- login (app/admin.py:7)" in find_callers(context, {"name": "verify_password"})
    assert "- verify_password (app/admin.py:11)" in find_callees(context, {"name": "login"})
    assert "No function named" in find_callers(context, {"name": "missing"})
    print("OK - callers and callees come from the graph, with file:line and code")


def test_execute_tool_call_never_raises_and_wraps_output(repo):
    tools = {tool.name: tool for tool in SECURITY_AGENT.tools}

    unknown = execute_tool_call(tools, "rm_rf", "{}", repo)
    bad_json = execute_tool_call(tools, "read_file", "{not json", repo)
    outside = execute_tool_call(tools, "read_file", json.dumps({"path": "../x"}), repo)

    assert "unknown tool 'rm_rf'" in unknown
    assert bad_json.count("Error:") == 1 and "outside the repository" in outside
    for output in (unknown, bad_json, outside):
        assert output.startswith("<<<TOOL_OUTPUT ") and "<<<END_TOOL_OUTPUT " in output
    print("OK - unknown tools, bad arguments and refused paths come back as wrapped errors")


def test_tool_output_is_truncated(tmp_path):
    (tmp_path / "big.py").write_text("\n".join(f"x_{i} = {i}" for i in range(5000)), encoding="utf-8")
    tools = {tool.name: tool for tool in SECURITY_AGENT.tools}

    output = execute_tool_call(tools, "read_file", json.dumps({"path": "big.py"}), ToolContext(repo_root=tmp_path))

    assert len(output) < MAX_TOOL_OUTPUT_CHARS + 200
    print("OK - a large tool result is capped before it reaches the prompt")


# A fake Groq client that plays a scripted list of replies: tool calls first, then the answer.
class ScriptedGroq:

    def __init__(self, replies: list) -> None:
        self.replies = replies
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request) -> SimpleNamespace:
        self.requests.append(request)
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        return SimpleNamespace(choices=[SimpleNamespace(message=reply)])


def tool_call_reply(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    call = SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))
    return SimpleNamespace(content=None, tool_calls=[call])


def answer_reply(findings: list) -> SimpleNamespace:
    return SimpleNamespace(content=json.dumps(findings), tool_calls=None)


FINDING = {
    "severity": "MEDIUM", "rule_id": "missing_authorization", "confidence": "low", "file_path": "app/admin.py",
    "line_number": 7, "title": "t", "description": "d", "evidence": "e", "suggestion": "s",
}


def build_state() -> dict:
    return {
        "pr_number": 1, "repo_full_name": "test/repo", "pr_diff": "+ def f(): pass\n", "changed_files": [],
        "review_context": "", "findings": [], "final_review_markdown": "",
    }


def test_agent_calls_a_tool_then_answers(repo):
    client = ScriptedGroq([tool_call_reply("c1", "find_auth_guards", {"path_prefix": "app"}), answer_reply([FINDING])])

    result = run_agent(SECURITY_AGENT, build_state(), client, repo)

    assert len(client.requests) == 2
    tool_message = client.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
    assert "dependencies=[Depends(require_admin)]" in tool_message["content"]
    assert result["findings"][0]["rule_id"] == "missing_authorization"
    print("OK - the tool result goes back to the model, then its answer is parsed")


def test_tool_calls_stop_at_the_limit(repo):
    keeps_asking = tool_call_reply("c", "search_code", {"text": "login"})
    client = ScriptedGroq([keeps_asking] * 10)

    result = run_agent(SECURITY_AGENT, build_state(), client, repo)

    assert len(client.requests) == SECURITY_AGENT.max_tool_calls + 1, "N tool turns, then one forced answer"
    assert client.requests[-1]["tool_choice"] == "none"
    assert client.requests[-1]["messages"][-1] == {"role": "user", "content": FINAL_ANSWER_REQUEST}
    assert result["findings"] == []
    assert result["llm_calls"] == SECURITY_AGENT.max_tool_calls + 1, "every tool round is a paid model call"
    print(f"OK - at most {SECURITY_AGENT.max_tool_calls} tool calls, then tool_choice='none'")


def test_no_tools_are_offered_without_their_resource(repo):
    no_context = ScriptedGroq([answer_reply([])])
    repo_only = ScriptedGroq([answer_reply([])])

    run_agent(SECURITY_AGENT, build_state(), no_context)
    run_agent(LOGIC_AGENT, build_state(), repo_only, repo)

    assert "tools" not in no_context.requests[0], "without a ToolContext the agent is a single plain call"
    offered = [tool["function"]["name"] for tool in repo_only.requests[0]["tools"]]
    assert offered == ["read_file", "find_call_sites", "find_exception_handlers"], "graph tools are hidden when there is no call graph"
    print("OK - only tools whose repo or graph exists are offered")


if __name__ == "__main__":
    print("Run under pytest: these tests use fixtures.")
