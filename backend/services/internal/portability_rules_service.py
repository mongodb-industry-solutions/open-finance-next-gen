from typing import List
from database.connection import MongoDBConnection
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


class PortabilityRulesService:
    """Service for retrieving portability underwriting rule tiers."""

    def __init__(self, connection: MongoDBConnection, db_name: str, collection_name: str):
        self.rules_collection = connection.get_collection(db_name, collection_name)

    def get_underwriting_rules(self) -> List[dict]:
        """Retrieve all portability underwriting rules.

        Returns:
            List[dict]: All rules with LoanSubTypes, amount ranges, paths, and tiers.
        """
        rules = list(self.rules_collection.find({}))
        logging.info(f"Retrieved {len(rules)} portability underwriting rules")
        return rules
