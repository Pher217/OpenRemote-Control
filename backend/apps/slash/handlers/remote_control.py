from django.utils import timezone

from apps.threads.models import Thread


def handle(thread, args):
    """Start a new chat session (thread) bound to the current chat/group.

    The universal `/openremote-control` (alias `/orc`) command. It opens a fresh
    session in the same channel the operator already chats in (Telegram, or
    WhatsApp/Slack/Signal via Matrix bridges); the surface layer rebinds the
    channel to the returned thread so the conversation simply continues in the
    operator's app of choice.
    """
    name = " ".join(args).strip() if args else ""
    if not name:
        name = f"Session {timezone.now():%Y-%m-%d %H:%M}"

    new_thread = Thread.objects.create(
        name=name,
        runtime=thread.runtime,
        runtime_mode=thread.runtime_mode,
        account=thread.account,
        project=thread.project,
        metadata=dict(thread.metadata or {}),
    )

    return {
        "ok": True,
        "message": f"Started new session: {name}",
        "new_thread_id": str(new_thread.id),
        "thread_name": name,
    }
