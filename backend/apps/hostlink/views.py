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
