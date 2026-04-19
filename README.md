# Voice Customer Support Agent (Zero Budget)

A web-based voice agent that lets customers talk to your support 24/7.
It can **book orders, raise complaints, take feedback, check order status,
cancel orders, track deliveries, and answer general questions** — all
running locally on your laptop with no paid services.

```
Browser mic  ──▶  SpeechRecognition (browser)  ──▶  FastAPI backend
                                                         │
                                                         ▼
                                       Ollama (Llama 3.2) ── decides task_type
                                                         │       + extracts fields
                                                         ▼
                                                SQLite ◀── handler executes
                                                         │
                                                         ▼
Browser speaker  ◀──  speechSynthesis (browser)  ◀──  reply text
```

## What you get

- Beautiful single-page web UI with a tap-to-talk mic.
- Browser-native STT and TTS (no installs, no API keys).
- Local LLM via Ollama (recommended), with a built-in keyword-based fallback
  so the demo works *even before you install Ollama*.
- **Dynamic task types** — the AI decides what the user wants. Add new
  task types by editing one JSON file (no Python edits needed).
- **Catch-all inquiries** — if the AI sees a request that doesn't fit a
  predefined task type, it can invent a new one on the fly and the agent
  logs it as a customer inquiry instead of saying "I can't help with that".
- SQLite database stores users, orders, complaints, feedback, inquiries,
  and full conversation history.

## Project layout

```
voice-customer-agent/
├── backend/
│   ├── main.py            # FastAPI app + endpoints
│   ├── telegram_bot.py    # Free voice + text channel via Telegram
│   ├── twilio_voice.py    # Real phone calls via Twilio (TwiML)
│   ├── llm.py             # Ollama client + rule-based fallback
│   ├── tasks.py           # Task-type loader + handlers
│   ├── task_types.json    # Declarative task-type registry (edit me!)
│   ├── seed.py            # Sample grocery catalog seeder
│   ├── stt.py             # Optional server-side Whisper
│   ├── db.py              # SQLAlchemy models
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── data/
│   └── support.db       # auto-created on first run
└── README.md
```

## Quick start (Windows / PowerShell)

### 1. Install Python deps

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. (Recommended) Install Ollama for smart replies

Download from https://ollama.com and then pull a small model:

```powershell
ollama pull llama3.2:3b
```

Leave Ollama running in the background. The app will detect it automatically.

> Skipping this step is fine — the backend will fall back to a simple
> keyword-based engine so you can still try the full flow.

### 3. Seed the sample catalog (recommended for testing)

From the `backend` folder:

```powershell
python seed.py            # populates 120 groceries, aliases, substitutes
python seed.py --reset    # wipe + reseed (deterministic - same data every time)
python seed.py --dump-sql # also writes data/seed.sql for inspection
```

Verify it loaded:

```powershell
curl http://localhost:8000/api/groceries?q=aloo
curl http://localhost:8000/api/groceries/1
```

### 4. Run the server

From the `backend` folder:

```powershell
python main.py
```

Open http://localhost:8000 in Chrome or Edge, allow microphone access,
and tap the mic.

### 5. (Optional) Share it on the public internet for free

Install Cloudflare Tunnel (`cloudflared`) and run:

```powershell
cloudflared tunnel --url http://localhost:8000
```

You'll get a public `https://*.trycloudflare.com` URL you can share.

## Try it

Some things to say (or click the suggestion chips):

- "I want to order 2 pizzas to 21 Oak Street"
- "Raise a complaint about order 1 — the food was cold"
- "I'd like to give 5 stars feedback, great service"
- "What's the status of order 1?"
- "Cancel order 1"
- "Track delivery 1"

The agent will ask follow-up questions for any missing info and then
confirm the action.

## Task types are dynamic — the AI decides what to do

The set of things the agent can do is declared in **`backend/task_types.json`**,
not hard-coded in Python. The AI reads this catalog every turn, picks the
best `task_type` for the user's request, extracts the fields that task
needs, and asks follow-up questions for anything missing.

Even better: the AI is allowed to **invent a brand-new `task_type`**
on the fly when the user wants something that isn't in the catalog. Those
get captured in the `inquiries` table so you can see the patterns and
later promote them to first-class task types with their own handlers.

### Add a new task type — JSON only (no code)

Edit `backend/task_types.json` and add an object:

```json
{
  "name": "schedule_callback",
  "label": "Schedule a callback",
  "description": "User wants someone to call them back at a specific time.",
  "required_fields": ["phone", "preferred_time"],
  "field_prompts": {
    "phone": "What number should we call you on?",
    "preferred_time": "What time works best?"
  },
  "examples": ["Can someone call me back at 6pm?"],
  "keywords": ["callback", "call me back"]
}
```

That's it — the AI will start routing to it on the next request.
The agent will save it as an inquiry automatically. **Hot-reload without
restarting** the server with:

```bash
curl -X POST http://localhost:8000/api/task-types/reload
```

### Promote an inquiry to a first-class task

When you want a task type to actually *do* something (write to its own
table, hit an external API, etc.) instead of just being captured as an
inquiry:

1. Write a `handle_<your_task>(fields, db)` function in `backend/tasks.py`.
2. Register it in the `HANDLERS` dict at the bottom of the file.

Now that task is fully wired up.

## Talk to the agent on Telegram (free voice channel)

Telegram is the truly-free stand-in for "real phone calls" — same feeling
of voice notes and chat, with zero per-minute cost. The bot reuses the
**same agent engine and the same SQLite database** as the web UI, so an
order placed on Telegram shows up in the web UI and vice-versa.

### 1. Get a bot token (3 minutes, free)

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot`, pick a name and a username.
3. Copy the API token BotFather gives you (looks like `123456:AA...`).

### 2. Set the token and run the bot

PowerShell:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:TELEGRAM_BOT_TOKEN = "PASTE_YOUR_TOKEN_HERE"
python telegram_bot.py
```

macOS/Linux:

```bash
cd backend
source .venv/bin/activate
export TELEGRAM_BOT_TOKEN="PASTE_YOUR_TOKEN_HERE"
python telegram_bot.py
```

You'll see `Connected as @your_bot`. Now open Telegram, search for your
bot's username, hit **Start**, and start chatting.

### Commands inside the chat

- `/start` — welcome + capabilities
- `/help` — example phrases
- `/reset` — clear conversation context

### Voice notes

Plain text works out of the box. To enable **voice notes**, install the
optional Whisper engine:

```powershell
pip install faster-whisper
```

The first voice note will download a ~150 MB model into your cache and
then every voice note is transcribed locally on your laptop — still zero
cost, no API keys.

### Run web + Telegram at the same time

The web server (`python main.py`) and the Telegram bot
(`python telegram_bot.py`) are independent processes that share the
same SQLite database. Start them in two terminals and you have two
channels live simultaneously.

## Take real phone calls (Twilio)

This turns the agent into a true 24/7 phone-line customer service rep:
customers dial a real number, the agent greets them, listens, and answers
in a natural voice. Same brain, same database — every order/complaint
placed over the phone shows up alongside web and Telegram interactions.

> **Cost note (zero-budget reality check):** Real phone numbers are *not*
> truly free anywhere. Twilio's free trial gives you ~$15 of credit, no
> card required for sign-up. A US number is **$1.15/month** + about
> **$0.0085/minute** for inbound calls. Twilio's basic Speech Recognition
> is **free** with the call. So the trial credit is enough for a number
> plus several hours of testing.

### Architecture (just HTTP, no WebSockets, no extra deps)

```
Caller phone
     │
     ▼
Twilio  ──POST──▶  /api/twilio/voice    (greeting + open <Gather>)
              ◀── TwiML ──
              ─ caller speaks ─
     ──POST──▶  /api/twilio/gather   (transcript)
              ─ runs through process_message() (same brain) ─
              ◀── TwiML <Say>reply</Say><Gather>... ──
     ─ caller speaks again ─        (loops)
```

### 1. Create a Twilio account + buy a number

1. Sign up at https://www.twilio.com/try-twilio (free trial credit).
2. In the console, go to **Phone Numbers → Buy a Number**, pick one with
   "Voice" capability, buy it (uses trial credit).
3. From **Account Info**, copy your **Auth Token** — you'll set it as an
   env var so the bot can verify webhook requests really came from Twilio.

### 2. Expose your laptop publicly (free)

Twilio needs to reach your machine over HTTPS. Use Cloudflare Tunnel:

```powershell
cloudflared tunnel --url http://localhost:8000
```

You'll get a public URL like `https://abc-xyz.trycloudflare.com`.
Keep this terminal open; the URL stays alive while it runs.

### 3. Wire your number to the agent

In the Twilio console, open your number's **Configure** tab and set:

| Field | Value |
|---|---|
| **A Call Comes In → Webhook** | `https://abc-xyz.trycloudflare.com/api/twilio/voice` |
| **HTTP method** | `POST` |
| **Call Status Changes (optional)** | `https://abc-xyz.trycloudflare.com/api/twilio/status` (POST) |

Save.

### 4. Run the server with the auth token

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:TWILIO_AUTH_TOKEN = "PASTE_YOUR_AUTH_TOKEN"
python main.py
```

(Skipping `TWILIO_AUTH_TOKEN` works for testing too — request signature
validation just becomes a no-op. Always set it for anything public.)

### 5. Call your number

The agent will answer, greet you, and start the conversation. Try:

- *"I want to order 2 kilos of aloo to 21 Oak Street"*
- *"What's the status of order 5?"*
- *"Cancel order 3"*
- *"Goodbye"* (ends the call)

Hang up at any time and the call-status webhook clears the session.

### Test without a phone (no Twilio account needed)

A dev-only `_simulate` endpoint lets you replay a Twilio call locally:

```powershell
curl -X POST http://localhost:8000/api/twilio/_simulate `
     -d "call_sid=demo1" `
     -d "text=I want to order 2 kg aloo to 21 Oak Street"
```

You'll get back the exact TwiML the real Twilio would receive.

### Tuning knobs (env vars)

| Variable | Default | What it does |
|---|---|---|
| `TWILIO_AUTH_TOKEN` | _(unset)_ | Validate every Twilio webhook signature. Recommended. |
| `TWILIO_VOICE` | `Polly.Joanna` | Any [Twilio TTS voice](https://www.twilio.com/docs/voice/twiml/say/text-speech). |
| `TWILIO_LANGUAGE` | `en-US` | Speech-recognition language (e.g. `en-IN` for Indian English). |
| `TWILIO_GATHER_TIMEOUT` | `5` | Seconds to wait for the caller to start speaking each turn. |
| `TWILIO_GREETING` | (built-in) | First sentence the agent says when answering. |
| `TWILIO_GOODBYE` | (built-in) | Sign-off line when the call ends. |

### Latency tips

Twilio's webhook timeout is ~15 seconds. If you're using Ollama on a
slow CPU, use a small model (e.g. `llama3.2:3b`) and keep the prompt
short. The rule-based fallback always responds instantly, so the agent
never falls silent on a slow LLM.

## Going further

| Want to… | Do this |
|---|---|
| Sub-second phone latency | Swap `twilio_voice.py` for a Twilio Media Streams (WebSocket) version that pipes audio through Whisper + Piper instead of TwiML `<Gather>`. |
| Use WhatsApp | Wrap Meta's WhatsApp Cloud API webhook around the same `/api/talk` endpoint. |
| Replace browser STT | Install `faster-whisper` and POST audio to `/api/transcribe`. |
| Move off SQLite | Swap the `create_engine(...)` URL in `db.py` for Postgres / Supabase. |
| Better TTS voice | Install [Piper](https://github.com/rhasspy/piper) and stream its audio. |

## License

MIT — do whatever you like.
