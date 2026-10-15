from review.agents.tools.repo_files import search_lines, under_prefix
from review.agents.tools.tool_definition import Tool, ToolContext


# Code that builds or runs SQL, so the agent can see whether queries elsewhere are parameterized.
SQL_MARKERS = (
    ".execute(",
    ".executemany(",
    ".executescript(",
    ".raw(",
    "text(",
    "SELECT ",
    "INSERT INTO",
    "UPDATE ",
    "DELETE FROM",
)


def find_sql_queries(context: ToolContext, arguments: dict) -> str:
    in_prefix = under_prefix(str(arguments.get("path_prefix", "")))
    hits = search_lines(context, SQL_MARKERS, lambda path: path.endswith(".py") and in_prefix(path))
    return "\n".join(hits) if hits else "No SQL query found."


FIND_SQL_QUERIES = Tool(
    name="find_sql_queries",
    description=(
        "List the lines that build or run SQL (cursor.execute, .raw(), text(), SELECT/INSERT/UPDATE/DELETE). "
        "Use it to follow user input into a query, or to see how the repo usually parameterizes queries."
    ),
    parameters={
        "type": "object",
        "properties": {"path_prefix": {"type": "string", "description": "optional folder to limit the search, e.g. app/db"}},
    },
    run=find_sql_queries,
    requires="repo",
)
