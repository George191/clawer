"""Separate Twitter author profiles from tweet records, including references."""
from copy import deepcopy

from pymongo import UpdateOne

from app.utils.record_id import resolve_record_id


def split_twitter_accounts(record):
    tweet = deepcopy(record)
    accounts = {}

    def visit(node):
        node.pop("download_urls", None)
        author = node.pop("author", None) or {}
        account_id = author.get("id") or node.get("author_id")
        if account_id is not None:
            account_id = str(account_id)
            if not account_id:
                raise ValueError("Twitter author ID cannot be empty")
            if node.get("author_id") is not None and str(node["author_id"]) != account_id:
                raise ValueError("Twitter author profile and author_id disagree")
            node["author_id"] = account_id
            profile = accounts.setdefault(account_id, {"id": account_id})
            for key, value in author.items():
                if key not in {"_id", "_meta", "id"} and value is not None and value != "" and value != [] and value != {}:
                    profile[key] = deepcopy(value)
        for reference in node.get("referenced_tweets") or []:
            if isinstance(reference.get("tweet"), dict):
                visit(reference["tweet"])
        # Reply content uses the same author/profile relationship.
        for reply in node.get("replies") or []:
            if isinstance(reply, dict):
                visit(reply)

    visit(tweet)
    return tweet, list(accounts.values())


def twitter_account_operations(accounts, now, existing=None):
    operations = []
    for account in accounts:
        account_id = str(account["id"])
        old = (existing or {}).get(account_id, {})
        changed_urls = [key for key in ("avatar_url", "banner_url")
                        if key in account and account[key] != old.get(key)]
        fields = {**account, "_meta.updated_at": now, "_meta.template": "tw_account",
                  "_meta.data_type": "social_media", "_meta.data_source": "twitter",
                  "_meta.record_id": resolve_record_id({"id": account_id}),
                  "_meta.sync_status": "pending"}
        if changed_urls or not old.get("_meta", {}).get("download_status"):
            fields["_meta.download_status"] = "pending"
        update = {"$set": fields, "$setOnInsert": {"_meta.created_at": now}}
        if changed_urls:
            update["$unset"] = {f"assets.{key}": "" for key in changed_urls}
        operations.append(UpdateOne({"id": account_id}, update, upsert=True))
    return operations


async def upsert_twitter_accounts(collection, accounts, now):
    """Account creation must succeed before the caller persists stripped tweets."""
    if accounts:
        cursor = collection.find({"id": {"$in": [str(account["id"]) for account in accounts]}},
                                 {"id": 1, "avatar_url": 1, "banner_url": 1, "_meta.download_status": 1})
        existing = {account["id"]: account for account in await cursor.to_list(length=None)}
        await collection.bulk_write(twitter_account_operations(accounts, now, existing), ordered=False)
