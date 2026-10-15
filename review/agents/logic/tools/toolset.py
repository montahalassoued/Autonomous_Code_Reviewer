from review.agents.logic.tools.find_exception_handlers import FIND_EXCEPTION_HANDLERS
from review.agents.tools.code_graph import FIND_CALLEES, FIND_CALLERS
from review.agents.tools.repo_files import READ_FILE


# Logic bugs often live at the boundary: who calls the changed code, what it calls, and who handles its errors.
LOGIC_TOOLS = (READ_FILE, FIND_CALLERS, FIND_CALLEES, FIND_EXCEPTION_HANDLERS)
