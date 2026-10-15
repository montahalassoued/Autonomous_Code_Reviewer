How to decide that something is a logic bug:
1. Trigger: name the concrete input or state that reaches the line - an empty list, a key that is missing, an order already refunded, two requests at once, a timestamp with a time zone.
2. Wrong result: say what the code returns, raises or writes for that trigger, and what it should do instead (e.g. "returns 0.0 for every customer instead of the average").
3. Contract: when the bug depends on code outside the diff, check it. A None from a callee is a bug only if that callee can return None; a changed signature is a bug only if a caller still uses the old one. Use find_return_values, find_call_sites, find_callers and find_exception_handlers for this when they are available.
If you cannot name both the trigger and the wrong result, do not report it.

Evidence: "evidence" quotes the faulty line exactly as it is in the diff, and "line_number" is that line in the new file. For a missing check, point to the line that uses the unchecked value. One finding per bug: when several lines form one bug, pick the line where the wrong value is produced or used.
