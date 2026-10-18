from review.diff_line_map import map_diff_lines


DIFF = """diff --git a/app/x.py b/app/x.py
--- a/app/x.py
+++ b/app/x.py
@@ -10,3 +10,4 @@ def f():
 a = 1
-b = 2
+b = 3
+c = 4
 d = 5
diff --git a/app/new.py b/app/new.py
--- /dev/null
+++ b/app/new.py
@@ -0,0 +1,2 @@
+x = 1
+y = 2
"""


def test_added_and_commentable_lines_per_file():
    lines = map_diff_lines(DIFF)

    assert lines["app/x.py"].added == {11, 12}
    assert lines["app/x.py"].commentable == {10, 11, 12, 13}, "removed lines take no new-file number"
    assert lines["app/new.py"].added == {1, 2}
    print("OK - new-side line numbers follow the hunk headers")


def test_a_deleted_file_has_no_lines():
    diff = "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-x = 1\n"

    assert map_diff_lines(diff) == {}
    print("OK - nothing to comment on in a deleted file")
