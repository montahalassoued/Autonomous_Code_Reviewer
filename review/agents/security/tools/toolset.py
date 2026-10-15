from review.agents.security.tools.find_auth_guards import FIND_AUTH_GUARDS
from review.agents.security.tools.find_sql_queries import FIND_SQL_QUERIES
from review.agents.tools.repo_files import READ_FILE, SEARCH_CODE


# Security checks what protects the changed code: auth guards and how SQL is built.
SECURITY_TOOLS = (READ_FILE, SEARCH_CODE, FIND_AUTH_GUARDS, FIND_SQL_QUERIES)
