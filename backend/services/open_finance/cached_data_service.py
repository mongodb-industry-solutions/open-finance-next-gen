import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from pymongo import ASCENDING

from database.connection import MongoDBConnection
from services.open_finance.customer_data_service import CustomerDataService

logger = logging.getLogger(__name__)


class CachedDataService:
    """Fetches consent-gated external data and caches it in the Leafy Bank database.

    Everything lands in a single collection (cachedExternalData), one document per
    resource, distinguished by ResourceType (ACCOUNT / PRODUCT / TRANSACTION). Each
    document is tagged with ConsentId so it can be purged in a single delete_many()
    when the consent is revoked or expires. Only DURATION_BASED consents are cached —
    ONE_TIME consents are consumed on read and don't fit a "delete when consent ends"
    lifecycle.
    """

    def __init__(
        self,
        connection: MongoDBConnection,
        db_name: str,
        customer_data_service: CustomerDataService,
        cached_data_collection_name: str,
    ):
        """Initialize the CachedDataService.

        Args:
            connection (MongoDBConnection): Connection to the Leafy Bank database.
            db_name (str): The Leafy Bank database name (holds the cache collection).
            customer_data_service (CustomerDataService): Retrieves consent-gated external data.
            cached_data_collection_name (str): Collection for all cached external data.
        """
        self.customer_data_service = customer_data_service
        self.cached_data_collection = connection.get_collection(db_name, cached_data_collection_name)
        # Reads filter by ConsentId (purge) or ConsentId + ResourceType (fetch one
        # resource type); this compound index covers both.
        self.cached_data_collection.create_index([("ConsentId", ASCENDING), ("ResourceType", ASCENDING)])
        # Dashboard reads filter by UserName (all of a user's cached data across banks).
        self.cached_data_collection.create_index([("UserName", ASCENDING), ("ResourceType", ASCENDING)])

    def fetch_and_cache(self, consent_id: str, user_name: str) -> Dict:
        """Fetch all consent-permitted external data and store it in the cache collection.

        Reuses CustomerDataService so the consent validation and permission gating
        stay in one place.

        Args:
            consent_id (str): The ConsentId to use for data retrieval.
            user_name (str): The username of the requesting user.

        Returns:
            Dict: The retrieved data plus consent metadata and per-resource cached counts.

        Raises:
            ValueError: If the consent is invalid, unauthorized, or not DURATION_BASED.
        """
        result = self.customer_data_service.retrieve_data_with_consent(
            consent_id=consent_id,
            user_name=user_name,
        )

        if result.get("consent_type") != "DURATION_BASED":
            raise ValueError(
                "Only DURATION_BASED consents can be cached. "
                f"Consent '{consent_id}' is {result.get('consent_type')}."
            )

        source_institution = result.get("source_institution")
        cached_at = datetime.now(timezone.utc)

        # Demo freshness: static source transactions get re-dated on every pull so the
        # dashboard always shows recent activity. Mutates the cached copy only — the
        # source external_transactions collection is untouched.
        self._shift_transaction_dates(result.get("transactions") or [])

        # Refresh semantics: clear any prior cache for this consent, then re-insert.
        # Keeps the cache idempotent across repeated fetches for the same consent.
        self.purge_consent_data(consent_id)

        docs: List[Dict] = []
        for resource_type, key in (("ACCOUNT", "accounts"), ("PRODUCT", "products"), ("TRANSACTION", "transactions")):
            for payload in (result.get(key) or []):
                docs.append(self._wrap(consent_id, user_name, source_institution,
                                       cached_at, resource_type, payload))

        if docs:
            self.cached_data_collection.insert_many(docs)

        cached_counts = {
            "accounts": len(result.get("accounts") or []),
            "products": len(result.get("products") or []),
            "transactions": len(result.get("transactions") or []),
        }
        logger.info(f"Cached data for consent {consent_id}: {cached_counts}")

        result["cached_counts"] = cached_counts
        return result

    def read_cached_data(
        self,
        user_name: str,
        resource_type: str = None,
        consent_id: str = None,
        consent_ids: List[str] = None,
    ) -> Dict:
        """Read a user's cached external data, grouped by source institution.

        Serves the dashboard without touching the live source, so one-time consents
        are never consumed and revoked/expired data (already purged) never appears.

        Args:
            user_name (str): The username whose cached data to return.
            resource_type (str, optional): Restrict to ACCOUNT / PRODUCT / TRANSACTION.
            consent_id (str, optional): Restrict to a single consent (one bank).
            consent_ids (List[str], optional): Restrict to a set of consents — used by
                the dashboard to show only the current browser session's connected banks,
                preventing cross-session duplicate data. Takes precedence over consent_id.

        Returns:
            Dict: {"user_identifier", "institutions": [{institution, consent_id,
                   accounts, products, transactions}]}.
        """
        query: Dict = {"UserName": user_name}
        if resource_type:
            query["ResourceType"] = resource_type
        if consent_ids is not None:
            query["ConsentId"] = {"$in": consent_ids}
        elif consent_id:
            query["ConsentId"] = consent_id

        # One entry per (institution, consent) pair, keyed for grouping.
        grouped: Dict = {}
        key_map = {"ACCOUNT": "accounts", "PRODUCT": "products", "TRANSACTION": "transactions"}
        for doc in self.cached_data_collection.find(query):
            key = (doc.get("SourceInstitution"), doc.get("ConsentId"))
            bucket = grouped.setdefault(key, {
                "institution": doc.get("SourceInstitution"),
                "consent_id": doc.get("ConsentId"),
                "accounts": [],
                "products": [],
                "transactions": [],
            })
            resource_key = key_map.get(doc.get("ResourceType"))
            if resource_key:
                bucket[resource_key].append(doc.get("Data"))

        return {"user_identifier": user_name, "institutions": list(grouped.values())}

    def compute_global_position(
        self, user_name: str, internal_balance: float = 0.0, consent_ids: List[str] = None
    ) -> Dict:
        """Compute total balance, total debt, and net worth across all connected banks.

        External balance = sum of AccountBalance across cached accounts. External debt =
        sum of ProductBalance across all cached external products — every product is a
        debt instrument (loans and credit cards). The caller supplies the internal Leafy
        Bank balance (resolved from the BIAN accounts), which is folded into total balance
        and shown as its own institution in the breakdown.

        Args:
            user_name (str): The username whose global position to compute.
            internal_balance (float): Total balance of the user's internal Leafy Bank accounts.
            consent_ids (List[str], optional): Restrict the external position to a set of
                consents (the current browser session's banks), matching the dashboard's
                cached-data view so totals never double-count cross-session duplicates.

        Returns:
            Dict: {"total_balance", "total_debt", "net_worth", "by_institution": [...]}.
        """
        cached = self.read_cached_data(user_name, consent_ids=consent_ids)

        by_institution: List[Dict] = []
        total_balance = internal_balance
        total_debt = 0.0

        if internal_balance:
            by_institution.append({
                "institution": "Leafy Bank",
                "balance": internal_balance,
                "debt": 0.0,
            })

        for bank in cached["institutions"]:
            balance = sum(acct.get("AccountBalance", 0) or 0 for acct in bank["accounts"])
            debt = sum(prod.get("ProductBalance", 0) or 0 for prod in bank["products"])
            total_balance += balance
            total_debt += debt
            by_institution.append({
                "institution": bank["institution"],
                "balance": balance,
                "debt": debt,
            })

        return {
            "total_balance": total_balance,
            "total_debt": total_debt,
            "net_worth": total_balance - total_debt,
            "by_institution": by_institution,
        }

    def purge_consent_data(self, consent_id: str) -> Dict:
        """Delete all cached data for a consent. Idempotent — 0 deletes is success.

        Called on consent revocation (synchronous) and by the expiry sweeper.

        Args:
            consent_id (str): The ConsentId whose cached data should be removed.

        Returns:
            Dict: Deleted count.
        """
        deleted = self.cached_data_collection.delete_many({"ConsentId": consent_id}).deleted_count
        if deleted:
            logger.info(f"Purged {deleted} cached document(s) for consent {consent_id}")
        return {"cachedExternalData": deleted}

    # Static demo transactions are compressed into this many days ending near today,
    # so they interleave with recent internal activity instead of fanning back months.
    _DEMO_WINDOW_DAYS = 21

    @staticmethod
    def _shift_transaction_dates(transactions: List[Dict]) -> None:
        """Re-date a batch of transactions in place, compressing them into a recent window.

        The original set spans months; the dashboard shows only the most recent rows,
        so spread-out external data gets buried under dense internal activity. This maps
        the whole batch proportionally into the last _DEMO_WINDOW_DAYS (newest near today,
        oldest ~3 weeks back), preserving order. A per-pull day of jitter on the end date
        keeps repeated pulls of the same static data looking freshly recent. No-op on an
        empty batch.

        Each transaction moves by a single whole-day delta (derived from its bookingDate)
        applied to all its date fields, so a transaction stays internally consistent:
        valueDate, bookingDate (string "YYYY-MM-DD"), createdAt (datetime), and every
        transactionDates[].date (datetime).
        """
        if not transactions:
            return

        def parse_day(value):
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                try:
                    return datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    return None
            return None

        days = [parse_day(t.get("bookingDate")) for t in transactions]
        present = [d.date() for d in days if d]
        if not present:
            return

        oldest, newest = min(present), max(present)
        span = (newest - oldest).days  # 0 when all transactions share one day

        window = CachedDataService._DEMO_WINDOW_DAYS
        end = datetime.now(timezone.utc).date() - timedelta(days=random.randint(0, 1))
        start = end - timedelta(days=window)

        for txn, original in zip(transactions, days):
            if not original:
                continue
            # Position within the original span (1.0 = newest), mapped into [start, end].
            fraction = 1.0 if span == 0 else (original.date() - oldest).days / span
            target = start + timedelta(days=round(fraction * window))
            delta = timedelta(days=(target - original.date()).days)

            for field in ("valueDate", "bookingDate"):
                field_date = parse_day(txn.get(field))
                if field_date:
                    txn[field] = (field_date + delta).strftime("%Y-%m-%d")
            if isinstance(txn.get("createdAt"), datetime):
                txn["createdAt"] = txn["createdAt"] + delta
            for entry in (txn.get("transactionDates") or []):
                if isinstance(entry.get("date"), datetime):
                    entry["date"] = entry["date"] + delta

    @staticmethod
    def _wrap(consent_id: str, user_name: str, source_institution: str,
              cached_at: datetime, resource_type: str, payload: Dict) -> Dict:
        """Tag an external-data document with consent metadata for caching."""
        return {
            "ConsentId": consent_id,
            "UserName": user_name,
            "SourceInstitution": source_institution,
            "ResourceType": resource_type,
            "CachedAt": cached_at,
            "Data": payload,
        }
