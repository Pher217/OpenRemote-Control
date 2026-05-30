import httpx
from django.conf import settings


def _base_url() -> str:
    return f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"


def redact_token(text: str) -> str:
    """Strip the bot token from any text before it reaches logs.

    httpx error messages embed the request URL, which contains the token in its
    path (.../bot<token>/...). Never log such a string un-redacted.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    return text.replace(token, "***") if token else text


async def send_message(chat_id, text) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.post(
            f"{_base_url()}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        resp.raise_for_status()


async def get_updates(offset, timeout=50):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=timeout + 10, write=5.0, pool=5.0)
    ) as client:
        resp = await client.get(
            f"{_base_url()}/getUpdates",
            params={"offset": offset, "timeout": timeout},
        )
        resp.raise_for_status()
        return resp.json().get("result", [])
