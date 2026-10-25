# Every agent model, and the fallback, must route to a configured provider, or every review would fail on its first call.
def check_agent_model_providers() -> None:
    from review.agents.agent_runner import agent_model, fallback_agent_model
    from review.graph import REVIEW_AGENTS
    from review.llm_client import provider_config_problems

    models = [agent_model(agent) for agent in REVIEW_AGENTS] + [fallback_agent_model()]
    problems = provider_config_problems(models)
    if problems:
        raise RuntimeError(" ".join(problems))


# Compiles the review graph once; each job brings its own repo (app/repo_checkouts.py), so no repo is cloned here.
def build_review_graph():
    from review.graph import build_graph

    check_agent_model_providers()
    return build_graph()
