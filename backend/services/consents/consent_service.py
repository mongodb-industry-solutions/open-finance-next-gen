from typing import Optional, List, Dict
from datetime import datetime, timezone, timedelta
from secrets import token_hex
from database.connection import MongoDBConnection
from services.consents.consent_state_machine import (
    validate_transition,
    validate_purpose,
    is_terminal,
    VALID_STATUSES
)

import logging

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Purpose -> default permissions mapping
# All credit portability purposes share the same permission set
CREDIT_PORTABILITY_PERMISSIONS = [
    "LOANS_READ",
    "ACCOUNTS_READ",
    "ACCOUNTS_BALANCES_READ",
    "REPAYMENT_HISTORY_READ",
    "CUSTOMER_IDENTIFICATION_READ",
    "TRANSACTIONS_READ",
]

PURPOSE_PERMISSIONS = {
    "PERSONAL_LOAN_PORTABILITY": CREDIT_PORTABILITY_PERMISSIONS,
    "PAYROLL_LOAN_PORTABILITY": CREDIT_PORTABILITY_PERMISSIONS,
    "VEHICLE_LOAN_PORTABILITY": CREDIT_PORTABILITY_PERMISSIONS,
    "FINANCIAL_ADVICE": [
        "ACCOUNTS_READ",
        "ACCOUNTS_BALANCES_READ",
        "TRANSACTIONS_READ",
    ],
}


class ConsentService:
    """This class provides methods to manage consent lifecycle in the database."""

    def __init__(self, connection: MongoDBConnection, db_name: str,
                 consents_collection_name: str, institutions_collection_name: str):
        """Initialize the ConsentService with MongoDB connection and collection names.

        Args:
            connection (MongoDBConnection): The MongoDB connection instance.
            db_name (str): The name of the database.
            consents_collection_name (str): The name of the consents collection.
            institutions_collection_name (str): The name of the institutions collection.

        Returns:
            None
        """
        self.consents_collection = connection.get_collection(db_name, consents_collection_name)
        self.institutions_collection = connection.get_collection(db_name, institutions_collection_name)

        # Ensure indexes exist
        self._ensure_indexes()

    def _ensure_indexes(self):
        """Create necessary indexes for the consents collection."""
        try:
            # Unique index on ConsentId
            self.consents_collection.create_index("ConsentId", unique=True)
            # Index for querying by user
            self.consents_collection.create_index("Consumer.UserName")
            # TTL index for auto-expiry (documents expire at ExpirationDateTime)
            self.consents_collection.create_index(
                "ExpirationDateTime",
                expireAfterSeconds=0
            )
            logging.info("Consent collection indexes ensured")
        except Exception as e:
            logging.warning(f"Index creation warning (may already exist): {e}")

    def _generate_consent_id(self, institution_name: str) -> str:
        """Generate a unique consent ID in URN format.

        Args:
            institution_name (str): The institution name to include in the URN.

        Returns:
            str: A URN-formatted consent ID (e.g., urn:greenbank:C1a2b3c4d5e6f7)
        """
        # Create a slug from institution name (lowercase, no spaces)
        slug = institution_name.lower().replace(" ", "")
        # Generate random hex token
        random_token = token_hex(7)
        return f"urn:{slug}:C{random_token}"

    # Demo mode: expiration_days value is treated as MINUTES (3-12)
    # This simulates Brazil Open Finance 12-month max in a demo-friendly timeframe
    # Use 0 for ONE_TIME consents (single use, no expiration - becomes CONSUMED after use)
    MAX_EXPIRATION_DAYS = 12   # Treated as minutes in demo
    MIN_EXPIRATION_DAYS = 3    # Treated as minutes in demo (for DURATION_BASED)
    DEFAULT_EXPIRATION_DAYS = 6

    def create_consent(
        self,
        consumer_user_name: str,
        consumer_user_id: str,
        purpose: str,
        source_institution_name: str,
        expiration_days: int = None,
        permissions: Optional[List[str]] = None
    ) -> dict:
        """Create a new consent record.

        Permissions are auto-assigned based on purpose. If permissions are provided,
        they must be a subset of the allowed permissions for the purpose (user can
        remove permissions but not add ones outside the purpose's set).

        Args:
            consumer_user_name (str): The username of the consumer.
            consumer_user_id (str): The user ID of the consumer.
            purpose (str): The purpose of the consent.
            source_institution_name (str): The name of the source institution.
            expiration_days (int): Consent duration. Defaults to 6.
                                   - 0 = ONE_TIME consent (single use, becomes CONSUMED after use)
                                   - 3-12 = DURATION_BASED consent (reusable until expiry)
                                   Demo mode: non-zero values treated as MINUTES for quick expiry.
            permissions (Optional[List[str]]): Subset of permissions to grant.
                                               If omitted, full default set for the purpose is used.

        Returns:
            dict: The created consent document.

        Raises:
            ValueError: If purpose is invalid, permissions invalid, institution doesn't exist,
                        or expiration out of range.
        """
        # Default expiration
        if expiration_days is None:
            expiration_days = self.DEFAULT_EXPIRATION_DAYS

        # Determine consent type based on duration
        if expiration_days == 0:
            # ONE_TIME consent: single use, becomes CONSUMED after data retrieval
            # No expiration - it's consumed after one use
            consent_type = "ONE_TIME"
            expiration = None
        else:
            # DURATION_BASED consent: reusable until expiration
            consent_type = "DURATION_BASED"
            # Validate expiration duration (Demo: 3-12, treated as minutes)
            if expiration_days > self.MAX_EXPIRATION_DAYS:
                raise ValueError(
                    f"Consent duration cannot exceed {self.MAX_EXPIRATION_DAYS}. "
                    f"Requested: {expiration_days}."
                )
            if expiration_days < self.MIN_EXPIRATION_DAYS:
                raise ValueError(
                    f"Consent duration must be at least {self.MIN_EXPIRATION_DAYS}. "
                    f"Requested: {expiration_days}."
                )
            expiration = datetime.now(timezone.utc) + timedelta(minutes=expiration_days)

        # Validate purpose
        validate_purpose(purpose)

        # Resolve permissions: auto-assign from purpose or validate provided subset
        default_permissions = PURPOSE_PERMISSIONS.get(purpose)
        if permissions is None:
            permissions = list(default_permissions)
        else:
            allowed = set(default_permissions)
            requested = set(permissions)
            extras = requested - allowed
            if extras:
                raise ValueError(
                    f"Permissions {extras} are not allowed for purpose '{purpose}'. "
                    f"Allowed permissions: {sorted(allowed)}"
                )
            if not requested:
                raise ValueError("At least one permission must be provided.")

        # Validate institution exists
        institution = self.institutions_collection.find_one(
            {"InstitutionName": source_institution_name}
        )
        if not institution:
            raise ValueError(f"Institution '{source_institution_name}' not found.")

        # Generate consent ID
        consent_id = self._generate_consent_id(source_institution_name)

        # Calculate timestamps
        now = datetime.now(timezone.utc)

        # Build the consent document
        consent_document = {
            "ConsentId": consent_id,
            "Status": "AWAITING_AUTHORISATION",
            "ConsentType": consent_type,
            "Consumer": {
                "UserName": consumer_user_name,
                "UserId": consumer_user_id
            },
            "Permissions": permissions,
            "Purpose": purpose,
            "SourceInstitution": {
                "InstitutionName": source_institution_name,
                "InstitutionId": str(institution.get("_id"))
            },
            "CreationDateTime": now,
            "StatusUpdateDateTime": now,
            "StatusHistory": [
                {
                    "Status": "AWAITING_AUTHORISATION",
                    "DateTime": now,
                    "Reason": "Consent created"
                }
            ]
        }

        # Only add ExpirationDateTime for DURATION_BASED consents
        if expiration:
            consent_document["ExpirationDateTime"] = expiration

        # Insert the document
        self.consents_collection.insert_one(consent_document)
        logging.info(f"Consent created: {consent_id} for user {consumer_user_name}")

        return consent_document

    def get_consent(self, consent_id: str) -> Optional[dict]:
        """Retrieve a consent by its ConsentId.

        Args:
            consent_id (str): The consent ID to look up.

        Returns:
            Optional[dict]: The consent document if found, otherwise None.
        """
        consent = self.consents_collection.find_one({"ConsentId": consent_id})
        if consent:
            logging.info(f"Consent found: {consent_id}")
        else:
            logging.info(f"Consent not found: {consent_id}")
        return consent

    def list_consents_for_user(self, user_name: str) -> List[dict]:
        """Retrieve all consents for a specific user.

        Args:
            user_name (str): The username to query consents for.

        Returns:
            List[dict]: A list of consent documents for the user.
        """
        consents = list(self.consents_collection.find({"Consumer.UserName": user_name}))
        logging.info(f"Found {len(consents)} consents for user {user_name}")
        return consents

    def update_status(
        self,
        consent_id: str,
        new_status: str,
        rejection_reason: Optional[Dict] = None
    ) -> Optional[dict]:
        """Update the status of a consent.

        Args:
            consent_id (str): The consent ID to update.
            new_status (str): The new status to transition to.
            rejection_reason (Optional[Dict]): Rejection details if status is REJECTED.

        Returns:
            Optional[dict]: The updated consent document, or None if not found.

        Raises:
            ValueError: If the transition is invalid.
        """
        # Fetch the consent
        consent = self.get_consent(consent_id)
        if not consent:
            return None

        current_status = consent.get("Status")

        # Validate the transition (raises ValueError if invalid)
        validate_transition(current_status, new_status)

        now = datetime.now(timezone.utc)

        # Build the update
        update_fields = {
            "Status": new_status,
            "StatusUpdateDateTime": now
        }

        # Add rejection details if status is REJECTED
        if new_status == "REJECTED" and rejection_reason:
            update_fields["Rejection"] = rejection_reason

        # Build status history entry
        history_entry = {
            "Status": new_status,
            "DateTime": now,
            "Reason": rejection_reason.get("Reason") if rejection_reason else f"Status changed to {new_status}"
        }

        # Perform the update
        result = self.consents_collection.update_one(
            {"ConsentId": consent_id},
            {
                "$set": update_fields,
                "$push": {"StatusHistory": history_entry}
            }
        )

        if result.modified_count > 0:
            logging.info(f"Consent {consent_id} status updated: {current_status} -> {new_status}")
            return self.get_consent(consent_id)

        return None

    def simulate_approval(self, consent_id: str) -> Optional[dict]:
        """Simulate approval of a consent (demo shortcut).

        Args:
            consent_id (str): The consent ID to approve.

        Returns:
            Optional[dict]: The updated consent document, or None if not found.
        """
        return self.update_status(consent_id, "AUTHORISED")

    def revoke_consent(self, consent_id: str) -> Optional[dict]:
        """Revoke an authorized consent.

        Args:
            consent_id (str): The consent ID to revoke.

        Returns:
            Optional[dict]: The updated consent document, or None if not found.
        """
        rejection_reason = {
            "Code": "CUSTOMER_MANUALLY_REVOKED",
            "Reason": "Consent revoked by user"
        }
        return self.update_status(consent_id, "REVOKED", rejection_reason)

    def reject_consent(self, consent_id: str, rejection_code: str, reason: str) -> Optional[dict]:
        """Reject a pending consent.

        Args:
            consent_id (str): The consent ID to reject.
            rejection_code (str): The rejection code.
            reason (str): The rejection reason.

        Returns:
            Optional[dict]: The updated consent document, or None if not found.
        """
        rejection_reason = {
            "Code": rejection_code,
            "Reason": reason
        }
        return self.update_status(consent_id, "REJECTED", rejection_reason)

    def consume_consent(self, consent_id: str) -> Optional[dict]:
        """Mark a consent as consumed after data retrieval.

        Args:
            consent_id (str): The consent ID to consume.

        Returns:
            Optional[dict]: The updated consent document, or None if not found.
        """
        return self.update_status(consent_id, "CONSUMED")
