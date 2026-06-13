from django.core.exceptions import ImproperlyConfigured

VALID_DELIVERY_MODES = frozenset({"progress", "all", "milestones_only"})


def validate_observe_delivery_mode(value: str) -> None:
    if value not in VALID_DELIVERY_MODES:
        raise ImproperlyConfigured(
            f"OBSERVE_DELIVERY_MODE must be one of {', '.join(sorted(VALID_DELIVERY_MODES))}; "
            f"got {value!r}"
        )
