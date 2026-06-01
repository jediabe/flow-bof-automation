# API keys

The AI prompt step needs access to one of: OpenAI, Anthropic, or OpenRouter.
You can also pick **manual** and write prompts yourself — no key needed.

## A consumer subscription is not an API key

This trips people up. To be unambiguous:

| Product                | Gives you            | Gives you API access? |
| ---------------------- | -------------------- | --------------------- |
| ChatGPT Plus / Pro     | the ChatGPT website  | **NO**                |
| OpenAI API             | api.openai.com keys  | yes                   |
| Claude Pro             | claude.ai chat       | **NO**                |
| Anthropic API          | api.anthropic.com    | yes                   |
| OpenRouter             | openrouter.ai keys   | yes                   |

If you only have a ChatGPT subscription, this tool can't use it. You need a
**separate** API key from <https://platform.openai.com/api-keys> (which is
billed separately from your ChatGPT subscription).

Same story with Claude: claude.ai logins won't work; you want
<https://console.anthropic.com/> and a billed API key.

## Recommended starter setups

**Cheapest** — OpenAI `gpt-4o-mini`. A typical product card costs well under
$0.01. Set `OpenAI model` to `gpt-4o-mini` in the Setup page.

**Best quality** — Anthropic `claude-3-5-sonnet-latest`. Higher cost, much
better prompts in our testing.

**One key for everything** — OpenRouter. One key works against most models.
Leave the model blank to use `openrouter/auto`, or lock to one (e.g.
`anthropic/claude-3.5-sonnet`).

## How keys are stored

When you save in the UI Setup page, your key is written to:

```
data/secrets.local.json
```

That file is excluded from git (`.gitignore`), excluded from the Docker
build context (`.dockerignore`), and excluded from `scripts/package_alpha.ps1`'s
output ZIP. It only ever exists on your machine.

The UI masks keys to the last 4 characters everywhere it shows them. The
CLI logs never print full keys.

## How keys are read at runtime

Loading priority:
1. **UI-saved settings** (`data/settings.local.json` + `data/secrets.local.json`).
2. **Environment variables** from `docker-compose.yml` (which pull from
   `.env` on your host).
3. **Hard-coded defaults** in the provider class.

So if you set `OPENAI_API_KEY` both in `.env` and in the UI, the UI wins.

## Do not share your keys

- Don't paste your key into a public chat, screenshot the Setup page with the
  key visible, or commit `data/secrets.local.json`.
- Don't ship the alpha ZIP with `data/secrets.local.json` inside —
  `package_alpha.ps1` strips it, but if you build your own ZIP some other way,
  double-check.
- Treat the key like a credit card: anyone who has it can spend on your
  account.

## Rotating a key

1. Revoke the old key on the provider's dashboard.
2. Open **Setup** in the UI, paste the new key, click Save.
3. Click Test API key. Done.
