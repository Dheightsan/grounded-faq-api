# grounded-faq-api

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

## License

MIT
