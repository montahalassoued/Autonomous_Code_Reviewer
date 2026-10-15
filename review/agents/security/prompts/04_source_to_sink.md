How to decide that something is a vulnerability - follow the data from source to sink:
1. Source: where the value comes from. Untrusted sources are request data (path, query, body, form, headers, cookies, uploaded files), webhook payloads, messages from a queue, rows other users wrote, files or archives the user supplied, and responses from third-party services. Constants, values from the server's own config or environment, and values the code just generated are trusted.
2. Sink: the dangerous operation it reaches (a query, a shell, eval, a template, an HTML response, a file path, an outbound URL, a redirect, a deserializer, a log line).
3. Guard: anything on the path that makes it safe - a parameterized query, an allowlist, a type that only accepts safe values (int, UUID, Enum, a Pydantic model with a pattern), escaping, shlex.quote with an argument list, secure_filename, a resolved-path check, a role or owner check.

Report a finding only when an untrusted source reaches a sink with no adequate guard, or when the change removes or weakens a guard. When the source is a function parameter and you cannot see the caller, say so and lower the confidence.

Evidence: "evidence" quotes the exact line of the sink (or of the missing check), copied from the diff. "description" names the source, the sink and the concrete attack: what the attacker sends and what they get (e.g. "a username of ' OR '1'='1 returns every user"). If you cannot name a realistic input that exploits it, do not report it.

Report the finding on the sink line in the new file, one finding per vulnerability: when several lines form one flaw, pick the line where the damage happens.
