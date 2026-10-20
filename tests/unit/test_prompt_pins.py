import hashlib

from review.agents.logic.agent import LOGIC_AGENT
from review.agents.quality.agent import QUALITY_AGENT
from review.agents.security.agent import SECURITY_AGENT

# The assembled prompts the last benchmark measured: the same hash as "prompt_sha256" in eval/results/*.json.
# A prompt, rules.py or examples.json edit changes it: re-run the benchmark for that agent before updating it here.
PINNED_PROMPT_HASHES = {
    "security": "b861f5e7d5096565",  # measured in llm_benchmark_20261020_183025
    "logic": "c8d2908cd6bff128",
    "quality": "86660253028aab3b",
}


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def test_each_agent_prompt_is_the_one_the_benchmark_measured():
    changed = {
        spec.name: prompt_hash(spec.prompt_template)
        for spec in (SECURITY_AGENT, LOGIC_AGENT, QUALITY_AGENT)
        if prompt_hash(spec.prompt_template) != PINNED_PROMPT_HASHES[spec.name]
    }
    assert not changed, (
        f"Prompt changed for {', '.join(changed)}. Re-run the benchmark for that agent "
        f"(venv/Scripts/python.exe -m eval.llm_benchmark --models groq:<model> --runs 3), compare it with the last report, "
        f"then set in PINNED_PROMPT_HASHES: {changed}"
    )
    print("OK - every production prompt is the one the last benchmark measured")
