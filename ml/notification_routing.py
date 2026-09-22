"""Which ntfy topic a notification goes to (AQ2, 2026-09-21).

Two audiences, two topics:
  * PUBLIC (secret NTFY_TOPIC_PUBLIC): gold prices, ups and downs, digests. Non-technical readers.
  * OPS    (secret NTFY_TOPIC, the pre-existing topic): everything about the system's health.

The design rule is that routing DEFAULTS TO OPS. A message reaches PUBLIC only if its trigger id is on
PUBLIC_ALLOWLIST below; anything else, including a trigger added tomorrow that nobody classified,
goes to OPS. The other direction is the dangerous one (an internal alert reaching non-technical
subscribers), so it takes an explicit, reviewable edit to this file, and tests/test_notification_
routing.py fails if any trigger id the module can emit is not classified here.

If NTFY_TOPIC_PUBLIC is not configured, PUBLIC messages fall back to the OPS topic rather than being
dropped, so the owner keeps receiving the digests while the public topic is being set up.
"""

from __future__ import annotations

from collections.abc import Mapping

PUBLIC = "public"
OPS = "ops"

# The ONLY trigger ids that may reach the public topic. Each one describes the gold price and nothing
# about the system. Adding an id here is a product decision: it must also have public-standard copy
# (ml/public_copy.py) and no directional forecast (tests/test_notification_copy_claims.py).
PUBLIC_ALLOWLIST: frozenset[str] = frozenset({"T1", "T2", "T3", "T4", "T8_MORNING", "T8_EVENING"})

# Every OTHER trigger id ml/notifications.py can emit, classified as OPS on purpose. A test requires
# that PUBLIC_ALLOWLIST | KNOWN_OPS covers every _make_alert id in the module, so a new trigger has
# to be classified by a person; the default for an id in neither set is still OPS.
KNOWN_OPS: frozenset[str] = frozenset(
    {
        "T5",  # companion model on backup: internal
        "T6",  # calibration unlocked: internal
        "T7",  # daily check, "System working normally": a heartbeat, not a price digest
        "T9",  # IBJA data stale
        "T9_ESCALATE",  # sustained IBJA outage
        "T10",  # feature-store snapshot gap
        "T11",  # Tanishq and IBJA both unavailable
        "T12",  # Tanishq self-hosted runner failing
        "T13",  # direction dataset stalled
    }
)

PUBLIC_TOPIC_ENV = "NTFY_TOPIC_PUBLIC"
OPS_TOPIC_ENV = "NTFY_TOPIC"


def audience_for(trigger_id: str) -> str:
    """PUBLIC only for an allowlisted id; OPS for everything else, known or not."""
    return PUBLIC if trigger_id in PUBLIC_ALLOWLIST else OPS


def resolve_topic(trigger_id: str, environ: Mapping[str, str]) -> tuple[str, str]:
    """Return (audience_actually_used, topic). topic is "" when nothing is configured.

    A PUBLIC message with no NTFY_TOPIC_PUBLIC set is delivered to the OPS topic (audience "ops"),
    never dropped and never sent anywhere else. If both topics are set to the same value the split
    is a no-op; that is reported by the caller, not silently accepted here.
    """
    ops = environ.get(OPS_TOPIC_ENV, "")
    if audience_for(trigger_id) == PUBLIC:
        public = environ.get(PUBLIC_TOPIC_ENV, "")
        if public:
            return PUBLIC, public
    return OPS, ops
