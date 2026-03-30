import logging
from database.connection import MongoDBConnection
from services.consents.consent_validator import ConsentValidator

logger = logging.getLogger(__name__)


class CustomerIdentificationService:
    """Consent-gated service for retrieving external customer identification (KYC) data."""

    def __init__(
        self,
        connection: MongoDBConnection,
        db_name: str,
        consents_collection_name: str,
        customer_identification_collection_name: str
    ):
        self.customer_identification_collection = connection.get_collection(
            db_name, customer_identification_collection_name
        )
        self.consent_validator = ConsentValidator(connection, db_name, consents_collection_name)

    def get_identification(self, consent_id: str, user_name: str) -> dict:
        """Retrieve customer identification data from an external bank via consent.

        Args:
            consent_id: The ConsentId authorizing data retrieval.
            user_name: The requesting user's UserName.

        Returns:
            dict: Customer identification records plus consent metadata.

        Raises:
            ValueError: If consent is invalid or missing required permission.
        """
        # Validate consent and get source institution
        consent, source_institution = self.consent_validator.validate_consent(
            consent_id, user_name, "CUSTOMER_IDENTIFICATION_READ"
        )

        # Query customer identification filtered by user + institution
        records = list(self.customer_identification_collection.find({
            "CustomerUser.UserName": user_name,
            "CustomerBank": source_institution
        }))

        logger.info(
            f"Retrieved {len(records)} customer identification records "
            f"for {user_name} from {source_institution}"
        )

        # Record data access in StatusHistory (audit trail)
        self.consent_validator.record_data_access(consent_id, "CUSTOMER_IDENTIFICATION")

        # Consume if one-time consent
        consent_status = self.consent_validator.consume_if_one_time(consent)

        return {
            "customer_identification": records,
            "consent_id": consent_id,
            "consent_status": consent_status,
            "source_institution": source_institution
        }
