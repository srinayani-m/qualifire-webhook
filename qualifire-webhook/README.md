# Qualifire Guardrail Webhook

Portkey webhook → Qualifire evaluate API. Drop-in Aporia replacement.

## Guardrails (3 custom)
- **prompt injection** — jailbreaks, system prompt leaks
- **Financial /tax/ legal advice** — investment, legal, tax
- **Medical** — diagnosis, medication, therapy

## Deploy to Render

1. Push to GitHub:
```bash
git init && git add . && git commit -m "init"
git remote add origin https://github.com/YOUR_USER/qualifire-webhook.git
git push -u origin main
```

2. Render → New → Web Service → connect repo
3. Set env vars: `QUALIFIRE_API_KEY` + `WEBHOOK_SECRET`

## Portkey Config

- **URL:** `https://qualifire-webhook.onrender.com/guardrail`
- **Header:** `Authorization: Bearer YOUR_WEBHOOK_SECRET`

## Critical: snake_case Only

```python
# WRONG: {"hateCheck": True}               → "unmonitored"
# RIGHT: {"prompt_injections": True}        → actually works
```
