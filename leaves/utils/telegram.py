import os
import requests

def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("TELEGRAM: missing env TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=8,
        )
        # ✅ 실패 원인 확인
        if resp.status_code != 200:
            print("TELEGRAM ERROR:", resp.status_code, resp.text[:300])
            return False

        return True

    except Exception as e:
        print("TELEGRAM EXCEPTION:", repr(e))
        return False

