import pytest

from app.syncer.worker import SyncWorker


class FakeMongo:
    def __init__(self, records):
        self.records = records
        self.synced = []

    async def get_ready_to_sync(self, template_name=None, limit=100):
        return self.records

    async def update_sync_status(self, template_name, data_type, record_id, sync_status):
        self.synced.append((template_name, data_type, record_id, sync_status))


class FakeKafka:
    def __init__(self, fail_record_id):
        self.fail_record_id = fail_record_id
        self.sent = []

    async def send_record(self, record):
        record_id = record["_meta"]["record_id"]
        if record_id == self.fail_record_id:
            raise RuntimeError("send failed")
        self.sent.append(record_id)


def _record(record_id):
    return {
        "_meta": {
            "record_id": record_id,
            "template": "google_patent",
            "data_type": "patent",
        }
    }


@pytest.mark.asyncio
async def test_process_batch_marks_only_successfully_sent_records(monkeypatch):
    worker = SyncWorker(template_name="google_patent")
    worker._mongo = FakeMongo([_record("ok"), _record("bad")])
    worker._kafka = FakeKafka(fail_record_id="bad")

    async def no_task_control(task_id):
        return None

    monkeypatch.setattr(
        "app.syncer.worker.ai_collect_store.get_task_control",
        no_task_control,
    )

    sent_count = await worker._process_batch()

    assert sent_count == 1
    assert worker._kafka.sent == ["ok"]
    assert worker._mongo.synced == [("google_patent", "patent", "ok", "synced")]
