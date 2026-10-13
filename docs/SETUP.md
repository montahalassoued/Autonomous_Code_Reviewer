# SETUP

Step-by-step for every account.

---

## 1) GitHub Copilot / Education Pack

1. Go to https://education.github.com/pack and verify your school/work email with GitHub.
2. Activate the education benefits (including Copilot where eligible).
3. Confirm you can use Copilot in your IDE.

---

## 2) Groq (LLM)

1. Go to https://console.groq.com/ and sign up with your email.
2. Create or select an API key.
3. Copy `GROQ_API_KEY` from the Groq console.

---

## 3) Voyage AI (Embeddings)

1. Go to https://voyageai.com/ and sign up.
2. Create an API key.
3. Copy it into `VOYAGE_API_KEY`.

---

## 4) Qdrant Cloud (Vector DB prod)

1. Go to https://cloud.qdrant.io/ and sign up.
2. Create a free cluster.
3. Copy:
   - `QDRANT_URL`
   - `QDRANT_API_KEY`

---

## 5) Langfuse (tracing + LLM evaluation, open source)

1. Pick where Langfuse runs:
   - **Langfuse Cloud:** sign up at https://cloud.langfuse.com. This is the easiest option.
   - **Self-hosted:** run Langfuse with its official Docker Compose setup (see the Langfuse docs, "Self-hosting").
2. Create a project named `autonomous-code-reviewer`.
3. In the project settings, create API keys, then copy `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`.
4. Set `LANGFUSE_HOST` to the cloud URL for your region, or to your self-hosted URL.

---

## 6) Railway (Deploy - backend)

1. Go to https://railway.app and log in.
2. Create a new project.
3. Deploy the FastAPI service.
4. Copy the deploy URL for webhook configuration.

---

## 7) Render (PostgreSQL free)

1. Go to https://render.com and log in with GitHub.
2. Create a new PostgreSQL database (free tier).
3. Copy `POSTGRES_URL` from the database settings.

---

## 8) ngrok (Local webhook testing)

1. Go to https://ngrok.com/ and sign up.
2. Install ngrok.
3. Run: `ngrok http 8000`
4. Use the generated public URL as your GitHub webhook target.

---

## Final step: .env

Copy `.env.example` → `.env`, paste all keys, and start the stack with Docker Compose.
