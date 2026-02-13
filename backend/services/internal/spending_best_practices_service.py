from typing import List
from database.connection import MongoDBConnection
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


class SpendingBestPracticesService:
    """Service for retrieving spending category definitions with MCC mappings."""

    def __init__(self, connection: MongoDBConnection, db_name: str, collection_name: str):
        self.spending_collection = connection.get_collection(db_name, collection_name)

    def get_best_practices(self) -> List[dict]:
        """Retrieve all spending best practice categories.

        Returns:
            List[dict]: All 9 spending categories with ideal %, min/max ranges, MCC codes.
        """
        categories = list(self.spending_collection.find({}))
        logging.info(f"Retrieved {len(categories)} spending best practice categories")
        return categories
