from apps.slash.handlers.account import handle as account_handle
from apps.slash.handlers.model import handle as model_handle
from apps.slash.handlers.stop import handle as stop_handle

HANDLERS = {
    "stop": stop_handle,
    "model": model_handle,
    "account": account_handle,
}


def get_handler(name):
    return HANDLERS.get(name)
