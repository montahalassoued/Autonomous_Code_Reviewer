# The GitHub App

The reviewer receives GitHub events through a GitHub App. Install it once on your account, on all repositories, and every push of code and every pull request of every repo is reviewed: no repo URL to configure, a new repo is covered the day it is created. Reviews are posted by `<app-name>[bot]`, with an installation token that lasts an hour.

```text
git push (any repo) ──► GitHub ──webhook──► /webhook/github ──► queue ──► worker
                                                                            │ installation token for that repo
                                                                            ▼
                                 checks out the repo at the pushed commit, reviews before..after,
                                 posts a comment on the commit (a pull request gets its summary comment)
```

## What is reviewed

| Event | Reviewed when | Posted as |
|---|---|---|
| Push | to a branch of a repo whose owner is in `ALLOWED_OWNERS` (or listed in `ALLOWED_REPOS`), adding or changing a code file | a comment on the pushed commit |
| Pull request | opened, synchronized or marked ready, not a draft, author is a collaborator | the PR's summary comment, edited on each push |

Pushes that delete a branch, push a tag, carry no new commit or touch only docs, images or config are skipped before any LLM call. A pushed commit is reviewed once per branch (the `reviews` table dedupes redeliveries). On Groq's free tier a review costs about 20-40k tokens, so about 5-10 reviewed pushes a day fit the daily quota.

## Create it (about 10 minutes)

1. github.com → your avatar → **Settings → Developer settings → GitHub Apps → New GitHub App**.
2. **GitHub App name:** e.g. `montaha-code-reviewer` (it comments as `montaha-code-reviewer[bot]`). **Homepage URL:** this repo's URL.
3. **Webhook:** Active. **Webhook URL:** `https://<your public URL>/webhook/github` (an ngrok or cloudflared tunnel to the compose gateway locally, the API's URL on Azure). **Webhook secret:** a long random string, the same value as `GITHUB_WEBHOOK_SECRET` in `.env`:
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
4. **Repository permissions:**
   - **Contents: Read and write.** Read clones the code; GitHub asks for write to create a commit comment. The reviewer never writes code, and its tokens expire after an hour. (A narrower option for later: post push reviews as a check run, which needs only Checks: write.)
   - **Pull requests: Read and write** (the PR summary comment).
   - **Metadata: Read-only** (set automatically; the collaborator check uses it).
5. **Subscribe to events:** **Push** and **Pull request**.
6. **Where can this GitHub App be installed?** **Only on this account**: nobody else can install it and spend your quota (`ALLOWED_OWNERS` refuses other owners anyway).
7. **Create GitHub App.** On the App's page, note the **App ID**, then **Private keys → Generate a private key**: a `.pem` file downloads. Keep it out of every repo.
8. **Install App** (left menu) → your account → **All repositories** → Install.

## Configure the reviewer

In `.env`:

```text
GITHUB_APP_ID=<the App ID>
GITHUB_APP_PRIVATE_KEY=<the .pem on one line, see below>
GITHUB_WEBHOOK_SECRET=<the webhook secret from step 3>
ALLOWED_OWNERS=<your GitHub login>
```

`GITHUB_APP_PRIVATE_KEY` holds the key on one line, with `\n` for each line break, which `.env` and cloud secrets accept. To print it that way:

```bash
python -c "import sys; print(open(sys.argv[1]).read().strip().replace(chr(10), chr(92) + 'n'))" ~/Downloads/<app-name>.<date>.private-key.pem
```

Or mount the file and set `GITHUB_APP_PRIVATE_KEY_PATH` to its path inside the container. `GITHUB_TOKEN` can stay for local tests without the App: when `GITHUB_APP_ID` and a key are set, the App wins.

The worker refuses to start with neither the App nor `GITHUB_TOKEN`, and an event without an installation id (sent by a repository webhook) is refused while the App is configured, so a repo is never read with the wrong credentials.

## Check it

- **App → Advanced → Recent Deliveries:** every event GitHub sent, the response code, and a **Redeliver** button. `202` = queued, `200` with `"ignored"` = skipped on purpose (the reason is in the body).
- `docker compose logs -f worker`: the checkout, the agents and `Review done`.
- The comment appears on the commit (push) or the pull request.
