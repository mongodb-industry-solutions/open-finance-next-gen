from database.connection import MongoDBConnection
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


class CreditBureauService:
    """Service for retrieving credit bureau scores from Leafy Bank's internal data."""

    def __init__(self, connection: MongoDBConnection, db_name: str, collection_name: str):
        self.credit_bureau_collection = connection.get_collection(db_name, collection_name)

    def get_credit_score(self, user_identifier: str) -> dict:
        """Retrieve credit bureau score for a user.

        Args:
            user_identifier: The user's UserName.

        Returns:
            dict: Credit score record or a NoHistory response.
        """
        record = self.credit_bureau_collection.find_one({
            "UserName": user_identifier
        })

        if record:
            logging.info(f"Credit score found for user {user_identifier}: {record.get('Score')}")
            return record

        logging.info(f"No credit score found for user {user_identifier}")
        return {
            "UserName": user_identifier,
            "Score": None,
            "Status": "NoHistory",
            "Bureau": None,
            "Factors": []
        }
