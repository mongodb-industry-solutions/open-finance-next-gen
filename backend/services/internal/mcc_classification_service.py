"""Service for classifying transactions by MCC code via MongoDB Atlas Vector Search."""

import logging
from typing import List, Optional
from database.connection import MongoDBConnection
import voyageai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class MCCClassificationService:
    """Classify untagged transactions against MCC reference data using vector search.

    Uses voyage-finance-2 embeddings and MongoDB Atlas $vectorSearch to match
    transaction merchant names + descriptions to the closest MCC code.
    """

    def __init__(self, connection: MongoDBConnection, db_name: str, collection_name: str):
        """Initialize with connection to the mcc_codes collection.

        Args:
            connection: Shared MongoDBConnection instance.
            db_name: Database name (e.g. leafy_bank_test).
            collection_name: Collection name (mcc_codes).
        """
        self.collection = connection.get_collection(db_name, collection_name)
        self.vo = voyageai.Client()
        self.model = "voyage-finance-2"
        self.index_name = "mcc_codes_vector_index"

    def classify_batch(self, transactions: List[dict]) -> List[dict]:
        """Classify a batch of untagged transactions via vector search.

        Args:
            transactions: List of dicts, each with at least:
                - merchant (str): Merchant name
                - description (str): Transaction description
                - amount (float, optional): Transaction amount
                - bank (str, optional): Source bank name

        Returns:
            List of dicts with original fields plus:
                - MCC, MCCDescription, CategoryId, CategoryName, confidence
        """
        if not transactions:
            return []

        # Build query texts from merchant + description
        query_texts = [
            self._build_query_text(
                txn.get("merchant_name", txn.get("merchant", "")),
                txn.get("description", "")
            )
            for txn in transactions
        ]

        # Batch embed all queries in a single API call
        logger.info(f"Embedding {len(query_texts)} transactions with {self.model}")
        embed_result = self.vo.embed(
            query_texts,
            model=self.model,
            input_type="query"
        )

        # Run vector search for each embedding
        results = []
        for txn, embedding in zip(transactions, embed_result.embeddings):
            match = self._vector_search(embedding)

            result = {
                "merchant": txn.get("merchant_name", txn.get("merchant", "")),
                "description": txn.get("description", ""),
                "amount": txn.get("amount", 0),
            }
            if txn.get("bank"):
                result["bank"] = txn["bank"]

            if match:
                result.update({
                    "MCC": match["MCC"],
                    "MCCDescription": match["MCCDescription"],
                    "CategoryId": match["CategoryId"],
                    "CategoryName": match["CategoryName"],
                    "confidence": round(match["score"], 4),
                })
            else:
                result.update({
                    "MCC": "",
                    "MCCDescription": "",
                    "CategoryId": "uncategorized",
                    "CategoryName": "Uncategorized",
                    "confidence": 0.0,
                })

            results.append(result)

        classified = sum(1 for r in results if r["CategoryId"] != "uncategorized")
        logger.info(f"Classified {classified}/{len(results)} transactions via vector search")

        return results

    def _build_query_text(self, merchant_name: str, description: str) -> str:
        """Build embedding query text from merchant name + description."""
        parts = []
        if merchant_name:
            parts.append(merchant_name)
        if description:
            parts.append(description)
        return " ".join(parts) if parts else "unknown transaction"

    def _vector_search(
        self, query_embedding: List[float], num_candidates: int = 20, limit: int = 1
    ) -> Optional[dict]:
        """Run $vectorSearch against the mcc_codes collection.

        Returns the top match with score, or None if no results.
        """
        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.index_name,
                    "path": "embedding",
                    "queryVector": query_embedding,
                    "numCandidates": num_candidates,
                    "limit": limit,
                }
            },
            {
                "$project": {
                    "MCC": 1,
                    "MCCDescription": 1,
                    "CategoryId": 1,
                    "CategoryName": 1,
                    "score": {"$meta": "vectorSearchScore"},
                    "_id": 0,
                }
            },
        ]

        results = list(self.collection.aggregate(pipeline))
        return results[0] if results else None

    def get_all_codes(self) -> List[dict]:
        """Return all MCC reference codes without embeddings.

        Returns:
            List of MCC code documents (embedding field excluded).
        """
        return list(self.collection.find({}, {"embedding": 0, "_id": 0}))
