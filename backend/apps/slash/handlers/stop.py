from apps.threads.models import Thread


def handle(thread, args):
    thread.status = Thread.StatusChoices.STOPPED
    thread.save(update_fields=["status"])
    return {"ok": True, "message": "Thread stopped."}
