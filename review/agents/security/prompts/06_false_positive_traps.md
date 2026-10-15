Do NOT report these - they look dangerous but are not vulnerabilities:
- A query with placeholders (?, %s, :name, $1) and the values passed separately, an ORM filter (filter_by, select().where), or an f-string that only inserts a constant table or column name chosen by the code.
- subprocess with a list of arguments and no shell=True, where the command itself is a constant; shell=True on a fully constant string.
- yaml.safe_load, json.loads, pickle on a file the application itself wrote to a private location, ast.literal_eval.
- Strings that only look like secrets: placeholders ("changeme", "xxx", "<your-key>", "test", "dummy"), values read from os.environ or settings, public identifiers (a client id, a public key, a URL without credentials), and fixtures inside test files.
- Code under tests/, test_*.py, conftest.py, fixtures or examples: verify=False, fake keys, debug=True and random values there are not production risks, unless the file is imported by production code.
- md5, sha1 or random used for non-security purposes: cache keys, checksums of files, ETags, deduplication, sampling, shuffling, jitter, test data. usedforsecurity=False confirms it.
- An endpoint protected by a router-level dependency, middleware, a class-based view permission or a decorator visible in the diff or the context; a public route that is meant to be public (health checks, login, signup, docs) and only reads non-sensitive data.
- Outbound requests to a URL built from a constant base and a value with a safe type (int, UUID) or from server configuration.
- Values rendered by a template engine with autoescape on (Jinja2 in Flask, Django templates) or returned as JSON.
- A value that the type already restricts: a path parameter typed int or UUID, an Enum, a Pydantic field with a pattern or Literal.

When unsure whether a guard exists, look it up with a tool if one is available; otherwise report with "low" confidence and say what you could not see - never report it as "high".
