from review.agents.quality.tools.find_blocking_calls_in_async import FIND_BLOCKING_CALLS_IN_ASYNC
from review.agents.quality.tools.find_loop_queries import FIND_LOOP_QUERIES
from review.agents.quality.tools.find_similar_functions import FIND_SIMILAR_FUNCTIONS
from review.agents.tools.related_tests import FIND_TESTS
from review.agents.tools.repo_files import READ_FILE, SEARCH_CODE


# Quality checks loops, async code, duplicates and tests outside the hunk before reporting.
QUALITY_TOOLS = (
    READ_FILE,
    SEARCH_CODE,
    FIND_TESTS,
    FIND_LOOP_QUERIES,
    FIND_BLOCKING_CALLS_IN_ASYNC,
    FIND_SIMILAR_FUNCTIONS,
)
