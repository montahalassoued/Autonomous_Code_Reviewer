# File purpose: Container image for the FastAPI review service.
# Inputs: Repository source code and Python dependency metadata (requirements.txt).
# Outputs: A deployable container image that serves the app on port 8000.

FROM python:3.11-slim

WORKDIR /app

# System packages needed by native Python deps: build tools for any
# source builds, and git for rag/ingestion.py's clone_repository().
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
