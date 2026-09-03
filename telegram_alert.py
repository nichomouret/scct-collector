"""Envoi de notifications Telegram (push). Vars : TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID."""
import os, sys
try:
    import requests
except ImportError:
    requests = None


def send_telegram(text: str) -> bool:
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not tok or not chat or requests is None:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": text,
                                "parse_mode": "HTML", "disable_web_page_preview": True},
                          timeout=15)
        if r.status_code != 200:
            print(f"  Telegram HTTP {r.status_code}: {r.text[:120]}", file=sys.stderr)
        return r.status_code == 200
    except Exception as e:
        print(f"  Telegram err {e}", file=sys.stderr)
        return False
