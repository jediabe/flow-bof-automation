# AI providers (Phase 3)

The **AI Product Intake** page in the Streamlit UI can generate BOF image / video prompts for a product card. You choose which API does the work via the `AI_PROVIDER` env var (and the matching API key).

> **A ChatGPT / Claude / OpenRouter web subscription does NOT cover API usage.** Each provider bills its API separately, by usage. You need an API key from the *developer* console, not your chat-app login. See "Costs" below.

## Provider matrix

| `AI_PROVIDER` | API key env var | Model env var | Where to get the key |
| --- | --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL` (default `gpt-4o-mini`) | https://platform.openai.com/api-keys |
| `anthropic` | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` (default `claude-3-5-sonnet-latest`) | https://console.anthropic.com/ |
| `openrouter` | `OPENROUTER_API_KEY` | `OPENROUTER_MODEL` (default `anthropic/claude-3.5-sonnet`) | https://openrouter.ai/keys |
| `manual` | — | — | (no API; you author the prompts yourself in the UI) |

All four providers expose the same Python interface:

```python
provider = get_provider("openai")    # from ai/prompt_generator.py
ok, msg = provider.is_configured()    # False if the API key isn't set
output = provider.generate_product_prompts(product_record_dict)
```

And every provider returns the **same JSON schema**:

```jsonc
{
  "product_name":      "...",
  "category":          "fitness | beauty | kitchen | ...",
  "store_environment": "Best Buy electronics section",
  "placement_type":    "retail shelf display | floor display | ...",
  "image_prompt":      "<full BOF image prompt>",
  "video_prompt":      "<full BOF motion prompt>",
  "hook":              "<one-sentence TikTok hook>",
  "caption":           "Product Name #hashtag1 #hashtag2",
  "warnings":          ["any concerns or missing fields"]
}
```

The UI shows the JSON in editable fields so you can review and revise before saving the product card.

## Setting up a provider

1. Get the API key for the provider you want.
2. Put it in your `.env` (in the project root, next to `docker-compose.yml`):

   ```env
   AI_PROVIDER=openai
   OPENAI_API_KEY=sk-...
   OPENAI_MODEL=gpt-4o-mini
   ```
3. Rebuild / restart the UI so it sees the new env:

   ```powershell
   docker compose restart ui
   ```
4. Open the UI → **AI Product Intake** → "AI provider" panel should show the selected provider with a green ready chip.

If the chip is red ("not configured"), the API key isn't reaching the container. Re-check your `.env`, the compose file pass-through, and the `docker compose restart`.

## Why subscriptions don't help

- **ChatGPT Plus / Pro / Team** is a *chat* subscription. It does not grant API credits on `platform.openai.com`. The API has its own billing (pay-as-you-go).
- **Claude Pro** is the same — chat access only. Anthropic API usage is billed by token via `console.anthropic.com`.
- **OpenRouter** has a one-stop billing system, but you still need to fund a credit balance or attach a card to your OpenRouter account; nothing else covers it.

## Costs (rough order of magnitude, change frequently — check the provider)

Each product card generation sends ~1.5 KB of prompt and gets back ~1 KB of JSON, so total ~2 500 tokens per product. With a current cheap model that's well under $0.01 per product on OpenAI's `gpt-4o-mini` or OpenRouter's small-model routing; a high-quality model like Claude Sonnet 3.5 lands around $0.01–0.03 per product. A 30-product batch is therefore tens of cents to a couple of dollars depending on model choice.

For real numbers consult each provider's pricing page — they update.

## Picking a model

The default for each provider is intentionally a cheap-enough model that does the job for BOF prompt drafting:

| Provider | Default model | Why |
| --- | --- | --- |
| openai | `gpt-4o-mini` | Cheapest of the JSON-mode-capable OpenAI models. |
| anthropic | `claude-3-5-sonnet-latest` | Best quality on prompt rewriting; "latest" alias survives Anthropic's monthly point releases. |
| openrouter | `anthropic/claude-3.5-sonnet` | OpenRouter's Sonnet endpoint. Swap to `openai/gpt-4o-mini` or any other OpenRouter slug for cheaper. |

Override per session in the UI ("AI provider" panel → model text input) or persistently via the matching `*_MODEL` env var.

## Safety / validation

After the API call, `validate_ai_output` in [ai/prompt_generator.py](../ai/prompt_generator.py) checks that `image_prompt` and `video_prompt` are present and non-empty. Anything else is best-effort:

- Unknown keys are flagged but kept (the UI displays them so you can review).
- Empty / missing optional keys are tolerated.
- If JSON parsing fails entirely, the UI shows the raw model output and lets you fix or regenerate.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Provider chip stays red | API key isn't set in the container. Check the `environment:` block in `docker-compose.yml` and your `.env`. Restart the `ui` service. |
| `openai.AuthenticationError` | Wrong key, or the key is from a different OpenAI account that has no funded balance. |
| `anthropic.RateLimitError` | You hit Anthropic's per-minute / per-day quota. Wait, or upgrade tier. |
| OpenRouter 402 | Credit balance is 0. Top up at https://openrouter.ai/credits. |
| AI returns markdown with the JSON in a code fence | Already handled by `extract_json` — both the fenced and bare forms parse fine. |
| `image_prompt` looks too generic | Add more detail to the product description and notes, or switch to a more capable model. |

## Privacy

Anything you put into a product card (TikTok URL, description, notes, reference image filenames) is sent verbatim to the provider you selected. Reference image *contents* are NOT sent — only the filenames — so the model doesn't see the pixels. If you need the model to see the image too, that's a future enhancement; for now the BOF prompt template tells the downstream image model to use the reference image as the source of truth.
