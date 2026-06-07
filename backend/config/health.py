from django.http import JsonResponse


def health(request):
    """Liveness probe for container/load-balancer healthchecks.

    Returns 200 without touching the DB or auth so it reflects "the web process
    is up and serving" — Postgres/Valkey have their own healthchecks.
    """
    return JsonResponse({"status": "ok"})
