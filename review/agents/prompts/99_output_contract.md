For each issue found, output one JSON object with these exact fields:
- "severity": one of "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"
- "rule_id": one of the rule ids listed above, or "other"
- "confidence": "high" when the problem is fully visible in the diff or the context, "medium" when it is likely, "low" when it depends on code you cannot see
- "file_path": the file the issue is in
- "line_number": the line number (integer) in the new version of the file, counted from the diff hunk header
- "title": a short title for the issue
- "description": what goes wrong, for which input or scenario, and its impact
- "evidence": the specific code snippet that shows the issue
- "suggestion": a concrete fix and why it helps, with a short code example when that makes it clearer

Output ONLY a JSON array of these objects, nothing else - no markdown, no explanation, no preamble. If there are no issues, output an empty JSON array: []
