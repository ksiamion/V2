import streamlit as st
from openai import OpenAI
from uuid import uuid4          # <-- add this line
from datetime import datetime
import json, re
import requests

# ---- Secure client ----
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# ---- System prompt ----
SYSTEM_PROMPT = """
You are an AI customer service agent. Your goal is to offer Internet support.

FIRST TURN:
- On the first assistant turn (no prior user messages), greet and request the Prolific ID ONLY:
  “Hello. I’m your virtual assistant. Please provide your Prolific ID below:”
- Do NOT include troubleshooting steps on this turn. Do NOT end the chat.

PROLIFIC ID CAPTURE (be flexible, retry politely):
- Treat as a valid Prolific ID anything that looks like a single alphanumeric token (letters/numbers, no spaces), typically 12+ characters. Accept forms like “ID: ABC123…”, “my id is …”, etc.
- If the user replies with anything that does NOT look like an ID (e.g., a question, greeting, or their issue), acknowledge briefly and ask again for the ID before proceeding:
  “Thanks! I’ll help in a moment—please enter your Prolific ID (e.g., ABC123DEF456).”
- If still no valid ID, ask once more, clearly:
  “Please provide your Prolific ID so I can continue.”
- Do not proceed to troubleshooting until an ID is provided. After you record it, confirm back:
  “Thanks, I’ve noted your Prolific ID.”

ASK FOR THE ISSUE (after ID is captured):
- Ask: “How can I assist you with your Internet issue today?”
- Be robust to variations in language and typos. Recognize any of the following as Internet/Wi-Fi/mobile data issues, even if phrased loosely: slow internet, buffering, lag, high ping, pages or videos not loading, Wi-Fi/WiFi/wi fi, disconnects/drops, can’t connect, hotspot/tethering, LTE/4G/5G, router/modem problems, signal/coverage, bandwidth/speed problems.

TROUBLESHOOTING (provide the EXACT text below whenever the user asks about Wi-Fi / slow internet / mobile internet issues):
"Sure, I can help you with a solution for slow mobile internet.

Here is a step by step guide to troubleshoot Home and Mobile WiFi issuez:

Steps for Mobile WiFi issue:

Restart your phone
o\tPower off the device, wait 10 seconds, and turn it back on.
Forget and reconnect to the WiFi network
o\tGo to Settings > WiFi > Select the network > Forget
o\tReconnect and re-enter the password carefully.
Check data balance (if using cellular hotspot)
o\tEnsure your data plan allows hotspot usage.
o\tSome carriers throttle hotspot speeds or restrict access after usage limits.

Steps for Home WiFi issue:

Forget the network and reconnect
o\t	Go to your device's WiFi settings, click the network name, and choose “Forget”
o\t	Then reconnect and re-enter the password.
Check router lights
o\t	Make sure Power, Internet, and WiFi lights are steady.
o\t	A blinking or red light may indicate an ISP issue.


”

AFTER THE STEPS:
- Thank the user and express hope that the answer was helpful.
- Instruct the user to proceed back to the survey to complete all questions about their experience:
  https://asu.co1.qualtrics.com/jfe/preview/previewId/62fdf4cc-a69f-4255-a321-4d795485d826/SV_3rutUOKtHWkQaA6?Q_CHL=preview&Q_SurveyVersionID=current
- Then end your message with a single line containing exactly:
[END_OF_CHAT]
Do not write anything after that token.

OUT-OF-SCOPE HANDLING:
- If the user asks anything unrelated to internet connectivity or Wi-Fi/mobile data troubleshooting, reply:
  "I am sorry. I was only trained to handle Internet connectivity issues."

STYLE & LANGUAGE:
- Be concise, polite, and clear.
- Gracefully handle typos and informal phrasing.
"""

st.title("Wireless Support Bot")

# Session state init
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
if "user_input" not in st.session_state:
    st.session_state.user_input = ""
if "chat_closed" not in st.session_state:
    st.session_state.chat_closed = False
if "bootstrapped" not in st.session_state:
    st.session_state.bootstrapped = False
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid4())
if "started_at" not in st.session_state:
    st.session_state.started_at = datetime.utcnow().isoformat() + "Z"
if "prolific_id" not in st.session_state:
    st.session_state.prolific_id = ""
if "saved_once" not in st.session_state:
    st.session_state.saved_once = False

END_TOKEN = "[END_OF_CHAT]"
# =========================
# HELPERS
# =========================

# ---- Colored chat bubbles (helper) ----
def render_bubble(role: str, text: str):
    # Colors: tweak to taste
    if role == "assistant":
        label = "Assistant"
        bg = "#E8F5FF"     # light blue
        border = "#B3E0FF"
        justify = "flex-start"   # left
    else:
        label = "You"
        bg = "#FFF4E5"     # light orange
        border = "#FFD8A8"
        justify = "flex-end"     # right

    # NOTE: If you expect HTML in messages, escape it before injecting.
    st.markdown(
        f"""
        <div style="display:flex; justify-content:{justify}; margin:6px 0;">
          <div style="
              max-width: 85%;
              padding: 10px 12px;
              background: {bg};
              border: 1px solid {border};
              border-radius: 14px;
              line-height: 1.45;
              white-space: pre-wrap;
              word-wrap: break-word;">
            <strong>{label}:</strong><br>{text}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def _messages_without_system():
    return [m for m in st.session_state.messages if m["role"] != "system"]

def _payload(include_system: bool = False):
    msgs = st.session_state.messages if include_system else _messages_without_system()
    return {
        "session_id": st.session_state.session_id,
        "started_at": st.session_state.started_at,
        "ended_at": datetime.utcnow().isoformat() + "Z" if st.session_state.chat_closed else None,
        "prolific_id": st.session_state.prolific_id or None,
        "messages": msgs,
    }

def _maybe_capture_prolific_id(text: str):
    # Best-effort: first user message is treated as an ID, otherwise find an alphanumeric 12+ token.
    if not st.session_state.prolific_id:
        if sum(1 for m in st.session_state.messages if m["role"] == "user") == 0:
            st.session_state.prolific_id = text.strip()
            return
        m = re.search(r"\b([A-Za-z0-9]{12,})\b", text)
        if m:
            st.session_state.prolific_id = m.group(1)

def _save_to_drive_once():
    """POST the full transcript to your Apps Script Web App once (no links shown to users)."""
    if st.session_state.saved_once:
        return
    try:
        base = st.secrets["WEBHOOK_URL"].rstrip("?")
        # Optional token support: if WEBHOOK_TOKEN is provided, append it; else just use base.
        token = st.secrets.get("WEBHOOK_TOKEN")
        url = f"{base}?token={token}" if token else base

        r = requests.post(url, json=_payload(False), timeout=10)
        if r.status_code == 200 and (r.text or "").strip().startswith("OK"):
            st.session_state.saved_once = True
        else:
            st.sidebar.warning(f"Admin note: webhook save failed ({r.status_code}): {r.text[:200]}")
    except Exception as e:
        st.sidebar.warning(f"Admin note: webhook error: {e}")

def _append_assistant_reply_from_model():
    response = client.chat.completions.create(
        model="gpt-4o",    # keep your current model
        messages=st.session_state.messages,
    )
    raw = response.choices[0].message.content or ""
    if END_TOKEN in raw:
        visible = raw.split(END_TOKEN)[0].rstrip()
        st.session_state.chat_closed = True
    else:
        visible = raw

    st.session_state.messages.append({"role": "assistant", "content": visible})

    # If the chat ended this turn, save logs to Drive (admin-only, silent)
    if st.session_state.chat_closed:
        _save_to_drive_once()

# =========================
# AUTO-START (assistant speaks first)
# =========================

if not st.session_state.bootstrapped:
    if len(st.session_state.messages) == 1:
        _append_assistant_reply_from_model()
    st.session_state.bootstrapped = True

# =========================
# RENDER HISTORY
# =========================

#replaced
#for msg in st.session_state.messages[1:]:
#    st.write(f"**{msg['role'].capitalize()}:** {msg['content']}")
# ---- Render history (skip system prompt) ----

for msg in st.session_state.messages[1:]:
    render_bubble(msg["role"], msg["content"])
# =========================
# INPUT HANDLING
# =========================

def send_message():
    if st.session_state.chat_closed:
        return
    text = st.session_state.user_input.strip()
    if not text:
        return
    _maybe_capture_prolific_id(text)
    st.session_state.messages.append({"role": "user", "content": text})
    _append_assistant_reply_from_model()
    st.session_state.user_input = ""
    # No st.rerun() in callbacks (no-op); Streamlit auto-reruns.

if st.session_state.chat_closed:
    st.info("🔒 End of chat. Thank you! Please return to the survey to complete all questions.")
    if st.button("Start a new chat"):
        sid = str(uuid4())
        st.session_state.clear()
        st.session_state.session_id = sid
else:
    st.text_input(
        "You:",
        key="user_input",
        placeholder="Type your message… (press Enter to send)",
        on_change=send_message,
    )
