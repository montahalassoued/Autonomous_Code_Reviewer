from code_index.file_classifier import is_secret_file, is_test_file, language_of


def test_test_files_are_recognized():
    assert is_test_file("tests/unit/test_x.py") and is_test_file("app/x_test.py") and is_test_file("pkg/tests/helpers.py")
    assert not is_test_file("app/x.py") and not is_test_file("tests/fixtures.json")
    print("OK - test_*.py, *_test.py and files under tests/ are tests")


def test_secret_files_are_never_indexed():
    assert is_secret_file("config/.env.production") and is_secret_file("certs/server.pem")
    assert language_of(".env") is None and language_of("deploy/id_rsa") is None
    print("OK - .env, keys and certificates get no language, so no row")


def test_secret_config_files_are_caught_but_code_is_not():
    for path in ("credentials.json", "config/secrets.yaml", "gcp/service-account.json", "prod.tfvars", "terraform.tfstate", ".git-credentials", "KEY.PEM", ".ENV"):
        assert is_secret_file(path), path
    for path in ("app/secrets.py", "auth/credential_store.py", "docs/secret-rotation.md"):
        assert not is_secret_file(path), path
    print("OK - secret-bearing data files are refused, code named after secrets stays reviewable")


def test_languages():
    assert language_of("app/x.py") == "python" and language_of("Dockerfile") == "dockerfile"
    assert language_of("config/settings.yml") == "yaml" and language_of("logo.png") is None
    print("OK - Python, known text files and nothing else")
