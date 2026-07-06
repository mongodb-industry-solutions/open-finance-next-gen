import logging
from datetime import datetime, timezone

from services.consents.consent_validator import ConsentValidator
from services.open_finance.cached_data_service import CachedDataService

logger = logging.getLogger(__name__)


class ConsentSweeper:
    """Periodically expires overdue consents and purges their cached data.

    Nothing fires automatically when a consent reaches its ExpirationDateTime, so
    this sweeper polls: it finds AUTHORISED consents whose expiry has passed,
    transitions them to EXPIRED, and deletes their cached data. Revocation is
    handled synchronously in the revoke endpoint — this only backstops time-based
    expiry.
    """

    def __init__(self, consent_validator: ConsentValidator, cached_data_service: CachedDataService):
        self.consents_collection = consent_validator.consents_collection
        self.consent_validator = consent_validator
        self.cached_data_service = cached_data_service

    def sweep(self) -> None:
        """Expire overdue consents and purge their cached data."""
        now = datetime.now(timezone.utc)
        overdue = self.consents_collection.find({
            "Status": "AUTHORISED",
            "ExpirationDateTime": {"$lte": now},
        })

        count = 0
        for consent in overdue:
            consent_id = consent.get("ConsentId")
            try:
                self.consent_validator._expire_consent(consent_id)
                self.cached_data_service.purge_consent_data(consent_id)
                count += 1
            except Exception as e:
                logger.error(f"Sweep failed to expire/purge consent {consent_id}: {e}")

        if count:
            logger.info(f"Consent sweep expired and purged {count} consent(s)")
