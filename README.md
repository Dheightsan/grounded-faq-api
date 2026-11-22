# grounded-faq-api

[![CI](https://github.com/Dheightsan/grounded-faq-api/actions/workflows/ci.yml/badge.svg)](https://github.com/Dheightsan/grounded-faq-api/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)

A small FastAPI service that answers questions only from a fixed fact file
(`data/store.json`, a fictional hardware store in Santiago). When the fact is not
there, it says `No tengo ese dato.` instead of guessing.

## Quick start

Python 3.12 or newer.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

It runs without any key. With no model configured, the answer is the text of the
best matching fact.

## Example

```bash
curl -s localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Cuanto cuesta el taladro?"}'
```

```json
{"answer":"El taladro percutor de 650 W cuesta $42.990.","grounded":true,"sources":["precio-taladro"]}
```

A question the data cannot answer:

```json
{"answer":"No tengo ese dato.","grounded":false,"sources":[]}
```

Other endpoints: `GET /health`, `GET /facts?topic=precios`.

## How it works

1. `app/retrieval.py` ranks facts with BM25 (lowercase, accents removed, short
   Spanish stopword list). If the best score is under the threshold, the service
   returns the fallback without calling a model.
2. If `MODEL_API_KEY` and `MODEL_BASE_URL` are set, `app/chat_client.py` sends the
   top facts to a chat-completions endpoint (`MODEL_NAME` picks the model) with a
   system message that allows only those facts or the exact fallback. The request
   times out after 20 seconds, and any failure drops back to the extractive answer.
3. `app/guard.py` checks the model answer. Every number (including `12.990` and
   `$12.990`), time, URL and email must appear in the retrieved facts. If not, the
   answer is replaced by the fallback and `grounded` is `false`.

## Tests

```bash
ruff check .
pytest -q
```

The tests use no network: the model is a fake injected through FastAPI
dependencies, and the HTTP client runs on `httpx.MockTransport`.

## WhatsApp agent

The same retrieval also runs a WhatsApp agent for a fictional barber shop
(`data/barberia/`). The shop, its address, hours and prices are all made up. It books appointments, keeps a small customer list and
answers questions from the shop's facts, with the same `No tengo ese dato.` rule.

- **Bookings.** The customer picks a service, a day and a free time from
  numbered lists. Free times come from the opening hours, the length of the
  service and the bookings already taken. The overlap check and the insert run
  in one write transaction, so two customers cannot get the same time.
- **Customer data.** The name is saved only after the customer says yes, and
  `BORRAR MIS DATOS` deletes the name, the bookings and the conversation.
- **Handoff.** `4` or "hablar con una persona" passes the chat to the shop. The
  bot answers once with "Te atiende una persona", then stays quiet until the
  customer writes `MENU` or 12 hours pass. Replies carry `handoff: true`.
- **Limits.** At most 2 active bookings per phone. A confirmation left for more
  than 30 minutes expires and the bot says so.
- **Questions.** Any other text goes to the grounded FAQ.
- **Webhook.** Meta's verification handshake, the `X-Hub-Signature-256` check
  (the webhook answers 503 until `WHATSAPP_APP_SECRET` is set), and retried
  deliveries answered only once. Message ids are kept for 7 days.

Try it in the terminal, no keys or WhatsApp account needed:

```bash
python -m app.cli
```

Or through the API:

```bash
export AGENT_API_KEY=change-me
uvicorn app.main:app
curl -s localhost:8000/agent/message \
  -H "X-API-Key: change-me" -H "Content-Type: application/json" \
  -d '{"from": "56911111111", "text": "hola"}'
```

| Endpoint | Auth | Use |
|---|---|---|
| `POST /agent/message` | `X-API-Key` | one message in, one reply out; optional `id` makes retries safe (n8n or any other channel) |
| `GET /agent/bookings?date=YYYY-MM-DD` | `X-API-Key` | bookings of one day, for reminders |
| `GET /whatsapp/webhook` | verify token | Meta's handshake |
| `POST /whatsapp/webhook` | app secret | messages from WhatsApp Cloud API |

Settings: `AGENT_API_KEY`, `AGENT_DB_PATH` (default `agent.db`), `AGENT_DATA_DIR`
(default `data/barberia`), and for WhatsApp `WHATSAPP_VERIFY_TOKEN`,
`WHATSAPP_APP_SECRET`, `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID`. Without
the token the agent still answers, it just does not send anything.

### With n8n

`n8n/whatsapp_agent.json` receives the WhatsApp webhook, calls `/agent/message`
with the API key (an n8n "Header Auth" credential named `Agent API key`, header
`X-API-Key`) and sends the reply. Meta's GET handshake is checked against the
API, so the verify token is kept in one place. `n8n/reminders.json` reads
tomorrow's bookings every day at 18:00 and sends a reminder template.

n8n passes the WhatsApp message id as `id`, so Meta's retries are answered once.
When the reply has `handoff: true`, the disabled `Notify the shop` node can
message the shop. Audio, images and stickers are ignored in this mode (n8n
sends no reply); the direct webhook answers them with a short notice.

The nodes that send to WhatsApp ship disabled. To go live, add a `WhatsApp
token` credential (`Authorization: Bearer <token>`), put the phone number id in
their URL and enable them. In this mode n8n does not check the webhook
signature; the direct mode above does.

## License

MIT
