// Voice customer support agent - frontend
// Uses the browser's built-in SpeechRecognition (STT) and speechSynthesis (TTS)
// so there are zero external dependencies.

const API = "/api";

const els = {
  chat: document.getElementById("chat"),
  micBtn: document.getElementById("micBtn"),
  transcript: document.getElementById("transcript"),
  hint: document.getElementById("hint"),
  enginePill: document.getElementById("enginePill"),
  resetBtn: document.getElementById("resetBtn"),
  textForm: document.getElementById("textForm"),
  textInput: document.getElementById("textInput"),
  welcome: document.querySelector(".welcome"),
};

// Persist a session id per browser so the backend can remember context.
const SESSION_KEY = "vca.session_id";
let sessionId = localStorage.getItem(SESSION_KEY);
if (!sessionId) {
  sessionId = "s_" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  localStorage.setItem(SESSION_KEY, sessionId);
}

// ---------------------------------------------------------------------------
// Health probe (so the user knows whether Ollama is running)
// ---------------------------------------------------------------------------
async function probeHealth() {
  try {
    const r = await fetch(`${API}/health`);
    const j = await r.json();
    els.enginePill.textContent = j.ollama ? "AI: Ollama" : "AI: rule-based";
    els.enginePill.title = j.ollama
      ? "Ollama is running locally - smart replies"
      : "Ollama not detected - using simple keyword fallback";
  } catch {
    els.enginePill.textContent = "offline";
  }
}
probeHealth();

// ---------------------------------------------------------------------------
// Chat rendering
// ---------------------------------------------------------------------------
function dismissWelcome() {
  if (els.welcome && els.welcome.parentElement) {
    els.welcome.remove();
  }
}

function addBubble(text, who, meta) {
  dismissWelcome();
  const div = document.createElement("div");
  div.className = `bubble ${who}`;
  div.textContent = text;
  if (meta) {
    const m = document.createElement("span");
    m.className = "meta";
    m.textContent = meta;
    div.appendChild(m);
  }
  els.chat.appendChild(div);
  els.chat.scrollTop = els.chat.scrollHeight;
  return div;
}

function addTyping() {
  dismissWelcome();
  const div = document.createElement("div");
  div.className = "bubble bot";
  div.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
  els.chat.appendChild(div);
  els.chat.scrollTop = els.chat.scrollHeight;
  return div;
}

// ---------------------------------------------------------------------------
// Text-to-speech (browser, free)
// ---------------------------------------------------------------------------
let voicesCache = [];
function loadVoices() {
  voicesCache = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
}
if (window.speechSynthesis) {
  loadVoices();
  window.speechSynthesis.onvoiceschanged = loadVoices;
}

function speak(text) {
  if (!window.speechSynthesis) return;
  // Cancel any in-flight speech so replies don't pile up.
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  // Prefer a natural English voice if one is available.
  const preferred =
    voicesCache.find((v) => /en-(US|GB|IN)/i.test(v.lang) && /natural|google|samantha|aria/i.test(v.name)) ||
    voicesCache.find((v) => /^en/i.test(v.lang));
  if (preferred) u.voice = preferred;
  u.rate = 1.02;
  u.pitch = 1.0;
  window.speechSynthesis.speak(u);
}

// ---------------------------------------------------------------------------
// Talk to backend
// ---------------------------------------------------------------------------
async function sendText(text) {
  if (!text || !text.trim()) return;
  addBubble(text, "user");
  const typing = addTyping();
  try {
    const r = await fetch(`${API}/talk`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, text }),
    });
    const j = await r.json();
    typing.remove();
    const meta =
      j.task_type && j.task_type !== "small_talk" ? `task: ${j.task_type}` : "";
    addBubble(j.reply, "bot", meta);
    speak(j.reply);
  } catch (err) {
    typing.remove();
    addBubble("Sorry, I lost the connection. Please try again.", "bot");
    console.error(err);
  }
}

// ---------------------------------------------------------------------------
// Speech recognition (STT)
// ---------------------------------------------------------------------------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let listening = false;

if (SR) {
  recognition = new SR();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = "en-US";

  recognition.onstart = () => {
    listening = true;
    els.micBtn.classList.add("listening");
    els.hint.textContent = "Listening… tap again to stop";
    els.transcript.textContent = "";
  };

  recognition.onresult = (event) => {
    let interim = "";
    let final = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const res = event.results[i];
      if (res.isFinal) final += res[0].transcript;
      else interim += res[0].transcript;
    }
    els.transcript.textContent = final || interim || "…";
    if (final) {
      stopListening();
      sendText(final.trim());
      els.transcript.textContent = "\u00a0";
    }
  };

  recognition.onerror = (e) => {
    console.warn("SR error", e.error);
    stopListening();
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      els.hint.textContent = "Microphone permission denied.";
    } else if (e.error === "no-speech") {
      els.hint.textContent = "I didn't catch that. Tap the mic and try again.";
    }
  };

  recognition.onend = () => stopListening();
} else {
  els.hint.textContent =
    "Your browser doesn't support speech recognition. Use Chrome/Edge, or just type below.";
  els.micBtn.disabled = true;
}

function startListening() {
  if (!recognition || listening) return;
  // Cancel any speaking so the mic doesn't pick up the bot's own voice.
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  try {
    recognition.start();
  } catch (err) {
    console.warn(err);
  }
}

function stopListening() {
  listening = false;
  els.micBtn.classList.remove("listening");
  els.hint.textContent = "Tap the mic to start speaking";
  try {
    recognition && recognition.stop();
  } catch {}
}

els.micBtn.addEventListener("click", () => {
  if (listening) stopListening();
  else startListening();
});

// ---------------------------------------------------------------------------
// Text input + quick chips + reset
// ---------------------------------------------------------------------------
els.textForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.textInput.value.trim();
  els.textInput.value = "";
  sendText(text);
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => sendText(chip.dataset.say));
});

els.resetBtn.addEventListener("click", async () => {
  try {
    await fetch(`${API}/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
  } catch {}
  els.chat.innerHTML = "";
  addBubble("Conversation cleared. How can I help?", "bot");
});
