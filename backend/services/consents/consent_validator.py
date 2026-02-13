from datetime import datetime, timezone
from database.connection import MongoDBConnection
from services.consents.consent_state_machine import can_retrieve_data
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


class ConsentValidator:
    """Shared consent validation logic for consent-gated endpoints."""

    def __init__(self, connection: MongoDBConnection, db_name: str, consents_collection_name: str):
        self.consents_collection = connection.get_collection(db_name, consents_collection_name)

    def validate_consent(self, consent_id: str, user_name: str, required_permission: str) -> tuple:
        """Validate a consent for data retrieval.

        Args:
            consent_id: The ConsentId to validate.
            user_name: The requesting user's UserName.
            required_permission: The permission required (e.g. CUSTOMER_IDENTIFICATION_READ).

        Returns:
            tuple: (consent_doc, source_institution_name)

        Raises:
            ValueError: If consent is invalid, expired, unauthorized, or missing permission.
        """
        # Load consent
        consent = self.consents_collection.find_one({"ConsentId": consent_id})
        if not consent:
            raise ValueError(f"Consent '{consent_id}' not found.")

        # Check expiration
        expiration_dt = consent.get("ExpirationDateTime")
        if expiration_dt:
            now = datetime.now(timezone.utc)
            if expiration_dt.tzinfo is None:
                expiration_dt = expiration_dt.replace(tzinfo=timezone.utc)
            if expiration_dt < now:
                self._expire_consent(consent_id)
                raise ValueError(f"Consent '{consent_id}' has expired.")

        # Check status is AUTHORISED
        status = consent.get("Status")
        if not can_retrieve_data(status):
            if status == "CONSUMED":
                raise ValueError(f"Consent '{consent_id}' has already been used (one-time consent).")
            elif status == "AWAITING_AUTHORISATION":
                raise ValueError(f"Consent '{consent_id}' is not yet authorized.")
            elif status in ("REJECTED", "REVOKED", "EXPIRED"):
                raise ValueError(f"Consent '{consent_id}' is no longer valid (status: {status}).")
            else:
                raise ValueError(f"Consent '{consent_id}' cannot be used (status: {status}).")

        # Verify ownership
        consent_user = consent.get("Consumer", {}).get("UserName")
        if consent_user != user_name:
            raise ValueError("Unauthorized: This consent does not belong to you.")

        # Check required permission
        permissions = consent.get("Permissions", [])
        if required_permission not in permissions:
            raise ValueError(
                f"Consent '{consent_id}' is missing required permission: {required_permission}."
            )

        # Extract source institution
        source_institution = consent.get("SourceInstitution", {}).get("InstitutionName")
        if not source_institution:
            raise ValueError("Consent is missing source institution information.")

        return consent, source_institution

    def consume_if_one_time(self, consent: dict) -> str:
        """Consume the consent if it's a ONE_TIME type.

        Args:
            consent: The consent document.

        Returns:
            str: The final consent status (CONSUMED or AUTHORISED).
        """
        if consent.get("ConsentType") == "ONE_TIME":
            now = datetime.now(timezone.utc)
            self.consents_collection.update_one(
                {"ConsentId": consent["ConsentId"]},
                {
                    "$set": {"Status": "CONSUMED", "StatusUpdateDateTime": now},
                    "$push": {"StatusHistory": {
                        "Status": "CONSUMED",
                        "DateTime": now,
                        "Reason": "One-time consent used"
                    }}
                }
            )
            logging.info(f"One-time consent {consent['ConsentId']} marked as CONSUMED")
            return "CONSUMED"
        return "AUTHORISED"

    def _expire_consent(self, consent_id: str) -> None:
        """Mark a consent as expired."""
        now = datetime.now(timezone.utc)
        self.consents_collection.update_one(
            {"ConsentId": consent_id},
            {
                "$set": {"Status": "EXPIRED", "StatusUpdateDateTime": now},
                "$push": {"StatusHistory": {
                    "Status": "EXPIRED",
                    "DateTime": now,
                    "Reason": "Consent duration exceeded"
                }}
            }
        )
        logging.info(f"Consent {consent_id} marked as EXPIRED")
