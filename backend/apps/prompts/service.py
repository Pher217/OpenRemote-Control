"""Prompt lifecycle service.

Creates prompts bound to a chat thread, looks them up by nonce, and resolves
them atomically with anti-replay protection via ``select_for_update``.
"""
import hashlib
import json
from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from apps.prompts.models import Prompt


def _generate_nonce() -> str:
    return uuid4().hex[:16]


def _compute_hash(prompt_type, question, options, trust_class, thread_id) -> str:
    canonical = json.dumps(
        {
            "prompt_type": prompt_type,
            "question": question,
            "options": options,
            "trust_class": trust_class,
            "thread_id": str(thread_id),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_prompt(
    thread,
    *,
    prompt_type,
    question,
    options=None,
    body="",
    trust_class=None,
    min_choices=0,
    max_choices=1,
    ttl_seconds=900,
    surface_message_ref=None,
) -> Prompt:
    if options is None:
        options = []
    if trust_class is None:
        trust_class = (
            Prompt.TrustClass.APPROVAL
            if prompt_type == Prompt.PromptType.APPROVAL
            else Prompt.TrustClass.DECISION
        )
    if surface_message_ref is None:
        surface_message_ref = {}

    nonce = _generate_nonce()
    prompt_hash = _compute_hash(prompt_type, question, options, trust_class, thread.id)
    expires_at = timezone.now() + timedelta(seconds=ttl_seconds)

    return Prompt.objects.create(
        thread=thread,
        prompt_type=prompt_type,
        question=question,
        options=options,
        body=body,
        trust_class=trust_class,
        min_choices=min_choices,
        max_choices=max_choices,
        nonce=nonce,
        prompt_hash=prompt_hash,
        expires_at=expires_at,
        surface_message_ref=surface_message_ref,
    )


def get_by_nonce(nonce) -> Prompt | None:
    try:
        return Prompt.objects.get(nonce=nonce)
    except Prompt.DoesNotExist:
        return None


def resolve(nonce, *, option_keys=None, text=None, by="") -> Prompt | None:
    # Anti-replay: the read-check-write below must be atomic. Without a row
    # lock, two concurrent resolves (multi-instance deploy, duplicate bot, or a
    # retry) can both observe status=PENDING and both record a response — which,
    # for a pty.inject approval, would dispatch the injection twice. select_for_update
    # inside a transaction serialises resolvers on the row: the second waits, then
    # sees status != PENDING and returns None.
    with transaction.atomic():
        try:
            prompt = Prompt.objects.select_for_update().get(nonce=nonce)
        except Prompt.DoesNotExist:
            return None

        now = timezone.now()
        if prompt.is_expired(now):
            if prompt.status == Prompt.StatusChoices.PENDING:
                prompt.status = Prompt.StatusChoices.EXPIRED
                prompt.save(update_fields=["status", "updated_at"])
            return None

        if prompt.status != Prompt.StatusChoices.PENDING:
            return None

        if option_keys is not None and prompt.options:
            valid_keys = {opt["key"] for opt in prompt.options}
            for k in option_keys:
                if k not in valid_keys:
                    return None
            count = len(option_keys)
            if count < prompt.min_choices or count > prompt.max_choices:
                return None

        prompt.record_response(option_keys=option_keys, text=text, by=by)
        prompt.save()
        return prompt
