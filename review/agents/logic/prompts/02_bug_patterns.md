Look for:
- Wrong or inverted conditions, wrong comparison operators, off-by-one errors in ranges, slices and loop bounds
- Missing handling of None, empty collections, zero, negative numbers or other edge cases the code will receive
- Results of external calls used without a check: an HTTP response used without checking its status, a database lookup that can return None or no rows, a file or key that may not exist
- Error handling that hides failures: swallowed exceptions, a wrong fallback value, an error path that leaves state half-updated
- Wrong return values or types, a missing return, a mutated argument, or a mutable default argument the caller doesn't expect
- Changed contracts: a new signature, return value or raised exception that existing callers in the context don't handle
- Logic that is only right for some states: feature flags, user roles, lifecycle states (e.g. a cancelled or refunded order) the code doesn't handle
- Wrong use of a library or API (wrong argument order, units, time zones, integer vs float division)
- Async and concurrency mistakes: a missing await, shared state modified without a lock, check-then-act races
