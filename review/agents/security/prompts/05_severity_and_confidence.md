Severity - judge what an attacker gains and who can do it:
- CRITICAL: an unauthenticated or low-privilege attacker gets remote code execution, reads or changes any user's data, or takes over accounts. Examples: SQL, command or code injection from request input; deserializing a request body with pickle; a missing authentication check on an endpoint that deletes or exports data; a live production secret committed in code.
- HIGH: serious impact with a precondition (the attacker needs an account, or the input source is an internal but untrusted service). Examples: IDOR, missing role check, SSRF, path traversal, stored XSS, JWT signature not checked, TLS verification turned off for an outbound call carrying credentials.
- MEDIUM: limited impact or extra conditions needed. Examples: open redirect, reflected XSS that needs a click, weak hash for a non-password secret, insecure cookie flags, CORS with credentials, ReDoS, sensitive data in logs, missing CSRF.
- LOW: defense in depth with no direct attack path, e.g. an unpinned dependency or verbose error text without secrets.
- INFO: do not use it for security findings; if it is not exploitable, do not report it.
Lower the severity one step when the input is only reachable by an admin, and raise it when the endpoint is public.

Confidence - how sure you are that it is exploitable as written:
- "high": the source, the sink and the absence of a guard are all visible in the diff or the context.
- "medium": the sink is visible and the source is very likely untrusted (a parameter named like request input, a route argument), but one hop is not shown.
- "low": the guard may exist in code you cannot see - a router dependency, middleware, a validator, a caller that passes a constant. Say in the description what you could not see.
If a tool is available, call it before reporting a "low" finding: the result often confirms the guard (drop the finding) or its absence (raise the confidence).
