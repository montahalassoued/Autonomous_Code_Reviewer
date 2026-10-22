import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "infra" / "gateway" / "refresh_github_hooks_allowlist.py"


# infra/ is not a package, so the script is loaded from its path.
def load_script():
    spec = importlib.util.spec_from_file_location("refresh_github_hooks_allowlist", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_allowlist_allows_each_hooks_range_then_denies_the_rest():
    allowlist = load_script().render_allowlist({"hooks": ["192.30.252.0/22", "2a0a:a440::/29"], "web": ["1.2.3.4/32"]})
    rules = [line for line in allowlist.splitlines() if not line.startswith("#")]
    assert rules == ["allow 192.30.252.0/22;", "allow 2a0a:a440::/29;", "deny all;"], "only the hooks ranges count"


def test_a_meta_answer_without_hooks_changes_nothing():
    with pytest.raises(ValueError, match="hooks"):
        load_script().render_allowlist({"web": ["1.2.3.4/32"]})


def test_a_value_that_is_not_an_ip_range_never_reaches_the_config():
    with pytest.raises(ValueError):
        load_script().render_allowlist({"hooks": ["1.2.3.0/24; include /etc/passwd"]})
