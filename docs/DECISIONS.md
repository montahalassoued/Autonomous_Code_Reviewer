# Decisions

- Groq over OpenAI: free tier, no credit card, LLaMA 3.3 70B matches GPT-4o quality for code
- voyage-code-2 over text-embedding-3-small: code-specific training, better function/symbol retrieval
- Contextual chunking: Anthropic research shows -67% retrieval failures vs naive chunking
- AST Knowledge Graph over vector-only: solves multi-hop architectural reasoning failures
- Hybrid search BM25+dense: exact symbol matching (BM25) + semantic intent (dense) combined
- RRF fusion: better than weighted sum for merging different retrieval signals
- BAAI/bge-reranker-v2-m3: CPU-only, no API cost, strong cross-encoder quality for code
- Corrective RAG loop: guarantees every review has sufficient context before agents run
- NetworkX over Neo4j: zero infrastructure cost, in-memory, sufficient for single repo graphs
- ChromaDB dev / Qdrant prod: zero setup locally, horizontal scaling in production
- Langfuse over LangSmith: open source, self-hostable, tracing + evals + prompt versioning, no per-trace cost
- Railway over DigitalOcean: GitHub login only, no credit card, Docker-native
