import os
import logging
import requests

logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def push(message: str) -> bool:
    """
    Send a message via Telegram Bot API.
    Fails silently (logs warning) — notifications must never crash the grading system.
    Returns True if sent successfully, False otherwise.
    """
    if not TOKEN or not CHAT_ID:
        logger.debug("Telegram not configured (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID missing). Skipping.")
        return False

    # Telegram limit: 4096 characters
    if len(message) > 4096:
        message = message[:4090] + "\n..."

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": message,
        }, timeout=10)
        if resp.status_code == 200:
            return True
        else:
            logger.warning(f"Telegram API returned {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"Telegram push failed: {e}")
        return False


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
    CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
    push("테스트 메시지입니다.")
