Do NOT report these - they look wrong but are not bugs:
- A case already handled: an earlier check or guard clause in the function, or a check in the caller visible in the diff, the context or a tool result.
- A value the type or framework guarantees: a parameter not typed Optional and never passed None by the callers you can see, a Pydantic field with a default or a constraint such as Field(gt=0), an Enum, a path parameter typed int, a column declared nullable=False.
- Behavior that a comment, docstring or name says is intended: a documented fallback value, // used on purpose for whole pages or buckets, a broad except at a worker or request boundary that logs the error and moves on.
- Correct idioms: `is None`, `is True` or a sentinel compared with is; dict.get(key, default); ceiling division -(-a // b); looping over list(items) or a copy while changing items.
- Code under tests/, test_*.py or conftest.py, unless production code imports it.
- Money held in a float when the diff only multiplies or returns it: using Decimal is a design choice, not a bug, unless you can show the amount that comes out wrong.
- Style, naming, type hints, performance or security issues that give no wrong result.
