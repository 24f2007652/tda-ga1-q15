# Acme Appliances order-ledger agent

This is a FastAPI service that loads the private ledger once, deduplicates the
latest version of each order, converts money to USD cents, and answers
plain-English questions through a deterministic query executor. If configured,
an OpenAI-compatible chat-completions model translates questions to the strict
query spec; without a model key, the built-in parser handles the supported
question forms.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env and set DATA_URL to the private URL supplied for this task.
uvicorn app:app --host 0.0.0.0 --port 8000
```

The service exposes `GET /health` and `POST /`. It supports CORS and OPTIONS
preflight. The response shape is always `{"answer": ...}`.

```powershell
curl.exe -X POST http://localhost:8000/ `
  -H "Content-Type: application/json" `
  -d '{"question":"What was the total revenue in USD from the North region in March 2026?"}'
```

## Environment variables

| Variable | Required | Description |
|---|---:|---|
| `DATA_URL` | yes | Private root data URL; never commit it |
| `LLM_API_KEY` | no | OpenAI-compatible API key |
| `LLM_BASE_URL` | no | Defaults to `https://api.openai.com/v1`; set the AI Pipe base URL if applicable |
| `LLM_MODEL` | no | Defaults to `gpt-4o-mini` |
| `PORT` | no | Hosting platform port; default command uses 8000 |

## Deploy on Render

1. Create a new **Web Service** from this repository.
2. Runtime: Python. Build command: `pip install -r requirements.txt`.
3. Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`.
4. Add `DATA_URL`, `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` as Render
   environment variables. Keep `DATA_URL` and the key private.
5. Deploy and verify `https://<service>.onrender.com/health`, then POST the
   sample question above to the service root.

## Tests

With the server running and `DATA_URL` set in the shell, run:

```powershell
python test.py
```

The test independently fetches and deduplicates the data, computes expected
answers, sends 20+ questions (including concurrent requests and edge cases),
and reports pass count and maximum latency.
