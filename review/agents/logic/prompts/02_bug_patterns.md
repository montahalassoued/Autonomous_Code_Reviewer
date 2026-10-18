Look for these in Python backend code:
- Conditions: `if status == "paid" or "refunded":` is always true; `is` on strings or numbers instead of ==; == on floats; < vs <= at a limit; range(), slice and loop bounds; empty input, zero or negative values
- None: dict.get, re.match, next(it, None), session.get, query .first() or a function with a path that returns None, used without a check; an HTTP response used without checking its status
- Results thrown away: `name.strip()` or `sorted(items)` alone on a line; a returned status or bool never checked
- Numbers: / vs //, int() truncating where round() is meant, round(2.5) giving 2, float money that is compared for equality, summed over many items or rounded to cents in a way that gives a wrong amount
- Datetimes: naive and aware datetimes compared or subtracted (TypeError), datetime.utcnow() or now() without tz where aware UTC is expected
- Iteration: a generator, map, zip or open file read twice (the second pass is empty); a list, dict or set changed while looping over it; a lambda made in a loop that uses the loop variable
- Errors and writes: a bare or broad except that hides a bug or returns a wrong fallback; raise inside except without `from err`; several writes with no transaction or rollback, so a failure halfway leaves the first ones applied
- State and contracts: a mutable default argument or an argument mutated unexpectedly; a cancelled, refunded or inactive state forgotten; a changed signature, return value or exception that existing callers don't handle
- Async: an async function called without await; asyncio.create_task with no reference kept; check-then-act on shared state with no lock
