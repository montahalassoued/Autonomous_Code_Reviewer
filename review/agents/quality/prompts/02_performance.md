Performance:
- Database queries inside loops (N+1)
- Quadratic work, such as membership tests on a list inside a loop
- Work repeated inside a loop that could be done once outside it (compiling a regex, opening a connection, reading config)
- Blocking I/O inside async code (requests, time.sleep, sync database drivers in an async def)
- Unbounded work: a query or endpoint that returns every row with no limit or pagination, a cache, list or listener registry that only grows
