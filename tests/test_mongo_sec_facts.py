import copy
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.storage.mongo_storage import MongoStorage
from app.utils.sec_financial_facts import FACT_COLLECTION


class MongoSecFactsTests(unittest.IsolatedAsyncioTestCase):
    def storage(self, existing=()):
        companies = MagicMock()
        companies.find.return_value.__aiter__.return_value = existing
        companies.bulk_write = AsyncMock()
        facts = MagicMock()
        facts.find.return_value.__aiter__.return_value = []
        facts.bulk_write = AsyncMock()
        storage = MongoStorage()
        storage._db = {"sec_edgar_company": companies, FACT_COLLECTION: facts}
        storage._initialized_collections = set(storage._db)
        return storage, companies, facts

    async def test_empty_fact_containers_save_company_without_fact_writes(self):
        empty_facts = (
            None,
            {},
            {"dei": {}, "us-gaap": {}},
            {"us-gaap": {"Assets": {"units": {}}}},
            {"us-gaap": {"Assets": {"units": {"USD": []}}}},
        )
        for facts in empty_facts:
            with self.subTest(facts=facts):
                storage, companies, observations = self.storage()
                record = {"cik": "0000000001", "name": "Example", "facts": facts}
                ids = await storage.save_records("sec_edgar_company", "company", ["cik"], [record])
                self.assertEqual(len(ids), 1)
                companies.bulk_write.assert_awaited_once()
                saved = companies.bulk_write.await_args.args[0][0]._doc
                self.assertEqual(saved["name"], "Example")
                self.assertNotIn("facts", saved)
                observations.bulk_write.assert_not_awaited()
                self.assertEqual(storage.last_save_stats["inserted"], 1)

    async def test_empty_taxonomies_do_not_block_existing_company_updates(self):
        record = {"cik": "0000000001", "name": "Updated", "facts": {"us-gaap": {}}}
        identity = MongoStorage()._resolve_record_id({"cik": record["cik"]})
        existing = {"cik": record["cik"], "name": "Old", "_meta": {"record_id": identity}}
        storage, companies, observations = self.storage([existing])
        await storage.save_records("sec_edgar_company", "company", ["cik"], [record])
        saved = companies.bulk_write.await_args.args[0][0]._doc
        self.assertEqual(saved["name"], "Updated")
        self.assertNotIn("facts", saved)
        observations.bulk_write.assert_not_awaited()
        self.assertEqual(storage.last_save_stats["updated"], 1)

    async def test_populated_facts_still_save_observations_including_zero(self):
        storage, companies, observations = self.storage()
        record = {
            "cik": "0000000001",
            "facts": {"us-gaap": {"Assets": {"units": {"USD": [{"val": 0, "accn": "a"}]}}}},
        }
        await storage.save_records("sec_edgar_company", "company", ["cik"], [record])
        saved_fact = observations.bulk_write.await_args.args[0][0]._doc
        self.assertEqual(saved_fact["value"], 0)
        self.assertEqual(saved_fact["cik"], record["cik"])
        self.assertEqual(saved_fact["concept"], "Assets")
        self.assertNotIn("facts", companies.bulk_write.await_args.args[0][0]._doc)

    async def test_malformed_facts_still_fail_without_company_writes(self):
        invalid_facts = (
            [],
            {"us-gaap": []},
            {"us-gaap": {"Assets": {}}},
            {"us-gaap": {"Assets": {"units": {"USD": {}}}}},
            {"us-gaap": {"Assets": {"units": {"USD": [{"accn": "a"}]}}}},
        )
        for facts in invalid_facts:
            with self.subTest(facts=facts):
                storage, companies, observations = self.storage()
                record = {"cik": "0000000001", "facts": copy.deepcopy(facts)}
                with self.assertRaises(ValueError):
                    await storage.save_records("sec_edgar_company", "company", ["cik"], [record])
                companies.bulk_write.assert_not_awaited()
                observations.bulk_write.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
