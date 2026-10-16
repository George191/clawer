"""Compatibility import for callers of the former record-oriented MongoClient."""

from app.records.mongo_store import MongoRecordStore as MongoClient

__all__ = ["MongoClient"]
