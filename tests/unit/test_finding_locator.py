from review.diff_line_map import map_diff_lines
from review.finding_locator import locate_findings

DIFF = "\n".join([
    "diff --git a/app/users.py b/app/users.py", "--- a/app/users.py", "+++ b/app/users.py", "@@ -10,3 +10,7 @@",
    " def get_connection():", "     return sqlite3.connect(\"app.db\")", " ",
    "+def find_user(username):",
    "+    conn = get_connection()",
    "+    query = f\"SELECT * FROM users WHERE name = '{username}'\"",
    "+    return conn.execute(query).fetchall()",
])


def finding(line: int, evidence: str, path: str = "app/users.py") -> dict:
    return {"agent": "security", "severity": "HIGH", "rule_id": "sql_injection", "confidence": "high", "file_path": path,
            "line_number": line, "title": "SQL injection", "description": "d", "evidence": evidence, "suggestion": "s"}


def test_added_lines_keep_their_code():
    assert map_diff_lines(DIFF)["app/users.py"].added_text[15] == "    query = f\"SELECT * FROM users WHERE name = '{username}'\""
    print("OK - the diff map knows what each added line says")


def test_a_miscounted_line_moves_to_the_code_its_evidence_quotes():
    reported_on_def = finding(13, "query = f\"SELECT * FROM users WHERE name = '{username}'\"")
    outside_function = finding(8, "query = f\"SELECT * FROM users  WHERE name = '{username}'\"")

    assert [f["line_number"] for f in locate_findings([reported_on_def, outside_function], DIFF)] == [15, 15]
    print("OK - the line follows the quoted code, whitespace aside")


def test_a_finding_is_left_alone_when_its_evidence_proves_nothing():
    already_right = finding(16, "return conn.execute(query).fetchall()")
    too_short = finding(13, "query")
    paraphrased = finding(13, "the username is formatted into SQL")
    other_file = finding(13, "query = f\"SELECT * FROM users WHERE name = '{username}'\"", path="app/orders.py")

    located = locate_findings([already_right, too_short, paraphrased, other_file], DIFF)

    assert [f["line_number"] for f in located] == [16, 13, 13, 13]
    print("OK - only a real quote of an added line moves a finding")


def test_multi_line_evidence_uses_its_first_quoted_line():
    evidence = "conn = get_connection()\nquery = f\"SELECT * FROM users WHERE name = '{username}'\""

    assert locate_findings([finding(20, evidence)], DIFF)[0]["line_number"] == 14
    print("OK - the first line the model quoted sets the location")
