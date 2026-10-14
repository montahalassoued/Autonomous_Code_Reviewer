# What the security agent may report; the rule_id also feeds the finding fingerprint.
SECURITY_RULES = {
    "sql_injection": "SQL built from untrusted input (CWE-89)",
    "command_injection": "untrusted input reaches a shell or subprocess (CWE-78)",
    "xss": "untrusted input rendered as HTML without escaping (CWE-79)",
    "insecure_deserialization": "pickle, yaml.load or similar on untrusted data (CWE-502)",
    "hardcoded_secret": "a key, password or token written in the code (CWE-798)",
    "ssrf": "the server fetches a URL controlled by the caller (CWE-918)",
    "path_traversal": "a file path or archive entry from untrusted input (CWE-22)",
    "weak_crypto": "weak hashing, fixed salt or IV, ECB mode (CWE-327)",
    "missing_authentication": "an endpoint that never checks who the caller is (CWE-306)",
    "missing_authorization": "an endpoint that never checks the caller may do this action (CWE-862)",
    "idor": "a resource loaded by id with no owner or tenant check (CWE-639)",
    "data_exposure": "sensitive fields or internal details sent to the client (CWE-200)",
    "secret_in_log": "secrets or personal data written to logs or errors (CWE-532)",
    "risky_dependency": "an unpinned, unknown or vulnerable new dependency (CWE-1104)",
}
