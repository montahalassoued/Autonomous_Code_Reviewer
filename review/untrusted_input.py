import secrets


DIFF_LABEL = "PULL_REQUEST_DIFF"
CONTEXT_LABEL = "REPOSITORY_CONTEXT"
TOOL_OUTPUT_LABEL = "TOOL_OUTPUT"
MARKER_OPENING = "<<<"


# Untrusted text between random-nonce markers: the author cannot guess the nonce, so cannot fake the end of the block.
def wrap_untrusted(text: str, label: str) -> str:
    nonce = secrets.token_hex(8)
    # A look-alike marker inside the content could still confuse the model, so "<<<" never appears in it.
    neutralized = text.replace(MARKER_OPENING, "<< <")
    return f"{MARKER_OPENING}{label} {nonce}>>>\n{neutralized}\n{MARKER_OPENING}END_{label} {nonce}>>>"
