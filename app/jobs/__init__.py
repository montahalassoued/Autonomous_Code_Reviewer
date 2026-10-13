"""
jobs package - the durable review-job pipeline between the webhook and the worker.

Inputs: ReviewJob objects created by app/api/webhook.py.
Outputs: queue and dedupe backends (in-memory or Azure Storage) consumed by
app/main.py (API side) and app/worker.py (worker side).

Why this package exists: the webhook must answer GitHub fast and never lose an
event, while a review takes minutes. A durable queue decouples the two.
"""
