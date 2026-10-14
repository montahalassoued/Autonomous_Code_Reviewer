"""
agents package - the three review agents, one per review category:
security_agent.py, logic_agent.py and quality_agent.py.

Each agent is only a prompt plus an AgentSpec; agent_runner.py
executes any of them the same way (prompt -> Groq call -> parsed and
validated findings). The order and parallelism of the agents are
decided by review/graph.py, not here.
"""
