"""Enroll endpoint: a host daemon calls this once to obtain a per-host token.

Rate-limiting note: add django-ratelimit or a reverse-proxy rule (e.g. nginx
limit_req) on POST /api/hostlink/enroll/ in production. This view itself does
not implement rate limiting to avoid a hard dependency on a rate-limit library.
"""

import hmac

from django.conf import settings
from django.utils.text import slugify
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.hostlink.auth import HostTokenAuthentication, IsAuthenticatedHost
from apps.hostlink.models import HostToken
from apps.hostlink.serializers import EnrollSerializer
from apps.hosts.models import Host


def _enroll_secret() -> str:
    return getattr(settings, "ORC_ENROLL_SECRET", "")


class EnrollView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        secret = _enroll_secret()
        if not secret:
            return Response(
                {"detail": "enrollment not configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        serializer = EnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        # Constant-time comparison to prevent timing attacks.
        if not hmac.compare_digest(
            d["enroll_secret"].encode(), secret.encode()
        ):
            return Response(
                {"detail": "invalid enroll_secret"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        host = _get_or_create_host(
            hostname=d["hostname"],
            os_value=d["os"],
            hw_uuid=d["hw_uuid"],
        )
        _, raw_token = HostToken.issue(host)

        payload = {
            "host_id": str(host.id),
            "host_slug": host.slug,
            "token": raw_token,
        }
        return Response(payload, status=status.HTTP_200_OK)


class HostApprovalView(APIView):
    """A driven session asks the operator to approve a tool use.

    The daemon's SDK ``can_use_tool`` callback POSTs {thread_id, title, preview};
    we create an APPROVAL prompt bound to that session's thread and deliver it —
    with Allow/Deny buttons — into the thread's own forum topic (reusing the
    connector delivery path). The daemon then polls :class:`HostApprovalResultView`
    for the decision. The thread MUST belong to the authenticated host (no
    cross-host approvals).
    """

    authentication_classes = [HostTokenAuthentication]
    permission_classes = [IsAuthenticatedHost]

    def post(self, request):
        from apps.connectors.service import _deliver
        from apps.prompts.models import Prompt
        from apps.prompts.service import create_prompt
        from apps.threads.models import Thread

        thread_id = request.data.get("thread_id", "")
        title = (request.data.get("title") or "Approve tool use?")[:500]
        preview = request.data.get("preview") or ""

        thread = Thread.objects.filter(id=thread_id).first()
        if thread is None:
            return Response({"detail": "unknown thread"}, status=status.HTTP_404_NOT_FOUND)
        if thread.host_id != request.user.id:
            # request.user is the authenticated Host; never approve another host's thread.
            return Response({"detail": "thread not owned by host"}, status=status.HTTP_403_FORBIDDEN)

        prompt = create_prompt(
            thread,
            prompt_type=Prompt.PromptType.APPROVAL,
            question=title,
            body=preview,
            options=[
                {"key": "allow", "label": "Allow"},
                {"key": "deny", "label": "Deny"},
            ],
            trust_class=Prompt.TrustClass.APPROVAL,
            ttl_seconds=3600,
            surface_message_ref={"action": "sdk_permission"},
        )
        _deliver(prompt)
        return Response({"nonce": prompt.nonce, "status": "pending"}, status=status.HTTP_201_CREATED)


class HostApprovalResultView(APIView):
    """Return the current decision for a tool-approval nonce (host-polled)."""

    authentication_classes = [HostTokenAuthentication]
    permission_classes = [IsAuthenticatedHost]

    def get(self, request, nonce):
        from apps.connectors.service import result

        return Response(result(nonce), status=status.HTTP_200_OK)


def _get_or_create_host(hostname: str, os_value: str, hw_uuid: str) -> Host:
    """Resolve host by hw_uuid stored in capabilities, or by slug derived from hostname."""
    # hw_uuid is the stable identity across renames; check it first.
    existing = Host.objects.filter(capabilities__hw_uuid=hw_uuid).first()
    if existing is not None:
        return existing

    base_slug = slugify(hostname) or "host"
    slug = base_slug
    n = 1
    while Host.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{n}"
        n += 1

    # Normalise OS to a known choice; fall back to linux.
    known = {c.value for c in Host.OsChoices}
    os_normalised = os_value.lower() if os_value.lower() in known else Host.OsChoices.LINUX

    host = Host.objects.create(
        slug=slug,
        name=hostname,
        os=os_normalised,
        capabilities={"hw_uuid": hw_uuid},
    )
    return host
