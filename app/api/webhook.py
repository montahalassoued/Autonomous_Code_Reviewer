"""GitHub webhook route handler.

Inputs: GitHub webhook payloads (PR opened/synchronized).
Outputs: Triggers review jobs via orchestration and returns an HTTP response.
"""

