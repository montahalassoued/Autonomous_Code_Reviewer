import pytest

from review.secret_redactor import has_secret, redact_secrets


# Built at runtime so secret scanners never flag the test file itself.
FAKE_SECRETS = {
    "github-token": "ghp_" + "A1b2" * 9,
    "groq-key": "gsk_" + "Z9y8" * 6,
    "aws-access-key": "AKIA" + "ABCD2345EFGH6789",
    "jwt": "eyJ" + "hbGciOiJI" + ".eyJ" + "zdWIiOiIx" + "." + "c2lnbmF0dXJl",
    "private-key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
}


@pytest.mark.parametrize("kind, secret", FAKE_SECRETS.items())
def test_each_known_format_is_redacted(kind, secret):
    redacted = redact_secrets(f"before {secret} after")

    assert secret not in redacted
    assert redacted == f"before [REDACTED:{kind}] after"
    print(f"OK - {kind} redacted, surrounding text kept")


def test_assigned_and_url_passwords_keep_their_context():
    redacted = redact_secrets('password = "hunter22x" and postgres://app:s3cretpw@db:5432/main')

    assert 'password = "[REDACTED:assigned-secret]"' in redacted
    assert "postgres://[REDACTED:url-password]@db:5432/main" in redacted
    print("OK - only the secret value is replaced, the key name and host stay readable")


def test_a_token_after_a_keyword_is_redacted_once():
    token = FAKE_SECRETS["github-token"]

    assert redact_secrets(f"token = {token}") == "token = [REDACTED:github-token]"
    print("OK - the specific kind wins over the generic assignment rule")


def test_git_shas_and_hashes_are_not_redacted():
    text = "commit 0123456789abcdef0123456789abcdef01234567 sha256 " + "f" * 64

    assert redact_secrets(text) == text and not has_secret(text)
    print("OK - no entropy guessing: SHAs and hashes are kept")


def test_known_values_are_redacted_exactly():
    redacted = redact_secrets("the key is plain-but-long-value", known_values=("plain-but-long-value", "short"))

    assert redacted == "the key is [REDACTED:known-secret]"
    print("OK - the worker's own secret values are redacted wherever they appear")
