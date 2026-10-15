from review.agents.security.tools.find_auth_guards import FIND_AUTH_GUARDS
from review.agents.security.tools.find_input_guards import FIND_INPUT_GUARDS
from review.agents.security.tools.find_security_settings import FIND_SECURITY_SETTINGS
from review.agents.security.tools.find_sql_queries import FIND_SQL_QUERIES
from review.agents.tools.repo_files import READ_FILE, SEARCH_CODE


# Security checks what protects the changed code: auth guards, input guards, app settings, how SQL is built.
SECURITY_TOOLS = (READ_FILE, SEARCH_CODE, FIND_AUTH_GUARDS, FIND_INPUT_GUARDS, FIND_SECURITY_SETTINGS, FIND_SQL_QUERIES)
