from apps.slash.handlers.account import handle as account_handle
from apps.slash.handlers.model import handle as model_handle
from apps.slash.handlers.stop import handle as stop_handle

# NOTE: /sessions is intentionally NOT registered here.  It is a global,
# operator-only command that must be handled at the authenticated Telegram-bot
# layer (service.handle_update) where from_user_id is available.  Wiring it
# here would expose the full fleet list to any unauthenticated dispatch_text
# caller (the per-thread slash dispatcher has no from_user_id).
HANDLERS = {
    "stop": stop_handle,
    "model": model_handle,
    "account": account_handle,
}


def get_handler(name):
    return HANDLERS.get(name)
