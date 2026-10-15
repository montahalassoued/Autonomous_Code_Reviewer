from review.agents.tools.code_graph import FIND_CALLEES, FIND_CALLERS
from review.agents.tools.repo_files import READ_FILE


# Logic bugs often live at the boundary: who calls the changed code, and what it calls.
LOGIC_TOOLS = (READ_FILE, FIND_CALLERS, FIND_CALLEES)
