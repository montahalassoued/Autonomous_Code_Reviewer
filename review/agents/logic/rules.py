# What the logic agent may report; the rule_id also feeds the finding fingerprint.
LOGIC_RULES = {
    "wrong_condition": "an inverted or wrong comparison or boolean condition",
    "off_by_one": "a range, slice or loop bound off by one",
    "unhandled_none": "a value that can be None is used without a check",
    "unhandled_edge_case": "empty, zero, negative or other edge input not handled",
    "unchecked_external_result": "an HTTP status, query result or file lookup used without a check",
    "swallowed_error": "an exception hidden, or a wrong fallback value returned",
    "wrong_return": "a wrong or missing return value or type",
    "mutable_default": "a mutable default argument shared between calls",
    "broken_caller_contract": "a changed signature, return value or exception that callers don't handle",
    "missed_state": "a flag, role or lifecycle state the code doesn't handle",
    "wrong_api_usage": "a library or API called with wrong arguments, units or time zones",
    "async_misuse": "a missing await or blocking misuse of async code",
    "race_condition": "shared state changed without a lock, or check-then-act",
}
