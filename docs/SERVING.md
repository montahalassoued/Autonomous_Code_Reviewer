# Serving a model yourself

The review agents call models through `review/llm_client.py`, the LLM gateway. A model named `provider:model` goes to that provider:

| Model in `.env` | Where the call goes |
|---|---|
| `openai/gpt-oss-120b` or `groq:openai/gpt-oss-120b` | Groq's API (`GROQ_API_KEY`) |
| `ollama:<model>` | Ollama on this machine (`OLLAMA_BASE_URL`, default `http://localhost:11434/v1`) |
| `vllm:<model>` | the OpenAI-compatible server in `VLLM_BASE_URL` (key in `VLLM_API_KEY`) |

So a self-hosted model needs no code change: serve it, set the base URL, and name it in `SECURITY_AGENT_MODEL` (or another agent's variable), or pass it to the benchmark with `--models`.

## Ollama or vLLM

| | Ollama | vLLM |
|---|---|---|
| Made for | one user on a laptop | serving many requests on a GPU |
| Runs on | CPU or GPU, compressed GGUF models | NVIDIA GPU |
| Throughput | low | high: continuous batching and PagedAttention keep the GPU busy with many requests at once |
| Metrics | few | Prometheus `/metrics` (requests waiting, tokens per second, KV-cache use) |

Measured on this project's laptop (4-core i7-1165G7, 2 GB MX450): `granite4.2:8b` (5.3 GB, does not fit the GPU) timed out after 900 s on one benchmark prompt (~5k tokens). An 8B model needs a real GPU; Groq answers the same call in 10-30 s.

## The benchmark on Kaggle's free GPU

`infra/serving/kaggle_vllm_benchmark.ipynb` serves `Qwen/Qwen2.5-Coder-7B-Instruct-AWQ` (4-bit, fits a 16 GB T4) with vLLM and runs `eval/llm_benchmark.py` against it, inside the notebook: the server is never exposed to the internet and no Groq key leaves your machine.

1. Create a Kaggle account and verify your phone number (Settings): GPU and internet access need it.
2. Kaggle → Code → New Notebook → File → Import Notebook → upload `infra/serving/kaggle_vllm_benchmark.ipynb`.
3. Right panel: Accelerator **GPU T4 x2**, Internet **on**. Not P100: recent vLLM does not support it.
4. Run all. The first run with `RUNS = 1` checks everything works (about 20-40 minutes); then set `RUNS = 3` for a result you can decide on.
5. Download `llm_benchmark_<date>.json` from Output and put it in `eval/results/`.
6. Compare with the Groq run of the same prompts, on your laptop, with no API call:

```bash
venv/Scripts/python.exe -m eval.llm_benchmark --replay eval/results/llm_benchmark_20261022_084512.json eval/results/<the vllm file>.json
```

`llm_benchmark_20261022_084512.json` is `gpt-oss-120b`, 3 runs, all 90 answers, on today's prompts (the `prompt_sha256` in both files must match; the replay warns when they don't).

The repo is cloned from GitHub, so push first: the notebook benchmarks what is on `main` there.

### What to read in the comparison

- **recall / precision / F1 per agent:** does the 7B model find the planted bugs, and only them.
- **clean pass:** no blocking false alarm on clean code.
- **invalid:** answers that are not a JSON array (they fail the agent in production).
- **latency:** per call on a T4; the server's own throughput is at the end of `vllm.log`.
- Cost: $0 and no daily quota, against Groq's free tier of about 200k tokens per model per day.

## The vLLM flags used

| Flag | Why |
|---|---|
| `--dtype half` | the T4 has no bfloat16 |
| `--max-model-len 16384` | longest prompt + answer; a ~5k prompt and a 4096-token answer fit |
| `--gpu-memory-utilization 0.90` | share of GPU memory for weights and KV cache |
| `--enable-auto-tool-choice --tool-call-parser hermes` | tool calls for the review agents (Qwen uses the hermes format) |
| `--api-key` | refuse requests without the key |
| `--served-model-name qwen2.5-coder-7b` | the short name used in `vllm:qwen2.5-coder-7b` |
