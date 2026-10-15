from review.agents.logic.tools.find_call_sites import FIND_CALL_SITES
from review.agents.logic.tools.find_exception_handlers import FIND_EXCEPTION_HANDLERS
from review.agents.logic.tools.find_return_values import FIND_RETURN_VALUES
from review.agents.tools.code_graph import FIND_CALLEES, FIND_CALLERS
from review.agents.tools.repo_files import READ_FILE


# Logic bugs live at the boundary: who calls the changed code and how, what it returns, who handles its errors.
LOGIC_TOOLS = (READ_FILE, FIND_CALLERS, FIND_CALLEES, FIND_RETURN_VALUES, FIND_CALL_SITES, FIND_EXCEPTION_HANDLERS)
