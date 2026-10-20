import difflib
import json
from pathlib import Path

from eval.benchmark.dataset_schema import join_lines, normalize_case


FIXTURE_REPO = Path(__file__).resolve().parents[1] / "fixtures" / "review_repo"
REVIEW_CASES = Path(__file__).resolve().parents[1] / "dataset" / "review_eval_cases.json"


# The new text of every file a case touches: a "find" must occur exactly once, so an edit can never land in the wrong place.
def apply_edits(case_id: str, edits: list[dict], repo: Path) -> dict[str, tuple[str, str]]:
    files: dict[str, tuple[str, str]] = {}
    for edit in edits:
        path = edit["file"]
        if "create" in edit:
            files[path] = ("", join_lines(edit["create"]) + "\n")
            continue
        old, new = files.get(path) or ((repo / path).read_text(encoding="utf-8"),) * 2
        find, replace = join_lines(edit["find"]), join_lines(edit["replace"])
        if new.count(find) != 1:
            raise ValueError(f"{case_id}: {path} must contain the 'find' text exactly once, found {new.count(find)}")
        files[path] = (old, new.replace(find, replace))
    return files


# The same shape as tools/github_tools.get_pr_diff: a git header, a/ and b/ names, then GitHub's patch hunks.
def unified_diff(files: dict[str, tuple[str, str]]) -> str:
    parts = []
    for path, (old, new) in files.items():
        hunks = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""))[2:]
        parts.append("\n".join([f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", *hunks]))
    return "\n".join(parts)


def _line_of(case_id: str, text: str, fragment: str) -> int:
    lines = [number for number, line in enumerate(text.splitlines(), start=1) if fragment in line]
    if len(lines) != 1:
        raise ValueError(f"{case_id}: '{fragment}' must be on exactly one line of the new file, found {len(lines)}")
    return lines[0]


# "at" (and "at_end") name the code of an issue; its line range is read from the edited file.
def _locate_issue(case_id: str, issue: dict, files: dict[str, tuple[str, str]]) -> dict:
    new_text = files[issue["file"]][1]
    start = _line_of(case_id, new_text, issue["at"])
    end = _line_of(case_id, new_text, issue["at_end"]) if "at_end" in issue else start
    return {**issue, "line_range": [start, end]}


def build_case(raw_case: dict, repo: Path = FIXTURE_REPO) -> dict:
    files = apply_edits(raw_case["id"], raw_case["edits"], repo)
    issues = [_locate_issue(raw_case["id"], issue, files) for issue in raw_case.get("expected_issues", [])]
    return normalize_case({**raw_case, "diff": unified_diff(files), "expected_issues": issues, "files": sorted(files)})


def load_review_cases(path: Path = REVIEW_CASES, repo: Path = FIXTURE_REPO) -> list[dict]:
    return [build_case(case, repo) for case in json.loads(path.read_text(encoding="utf-8"))["cases"]]
