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

## 3) Langfuse (tracing + LLM evaluation, open source)

Planned for Phase 3: no code sends traces yet, so you can skip this section for now.

1. Pick where Langfuse runs:
   - **Langfuse Cloud:** sign up at https://cloud.langfuse.com. This is the easiest option.
   - **Self-hosted:** run Langfuse with its official Docker Compose setup (see the Langfuse docs, "Self-hosting").
2. Create a project named `autonomous-code-reviewer`.
3. In the project settings, create API keys, then copy `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`.
4. Set `LANGFUSE_HOST` to the cloud URL for your region, or to your self-hosted URL.

---

## 4) Deploy (Azure Container Apps)

Follow [infra/README.md](../infra/README.md). It creates the Azure resources and connects the GitHub Actions deploy.

---

## 5) ngrok (Local webhook testing)

1. Go to https://ngrok.com/ and sign up.
2. Install ngrok.
3. Run: `ngrok http 8000`
4. Use the generated public URL as your GitHub webhook target.

---

## Final step: .env

Copy `.env.example` → `.env`, paste all keys, and start the stack with Docker Compose.
