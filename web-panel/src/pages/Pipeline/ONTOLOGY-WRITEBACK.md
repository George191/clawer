# External transaction writeback

A project owner or administrator who committed a transaction can open **Transactions → My transactions → Writeback**, select a configured receiver, and choose **Send transaction**. A platform administrator must also be an active member of the project. The service checks current record, field and marking access and the transaction's original markings on every request. An undone transaction or superseded record or link requires a new transaction before sending.

## Configure a receiver

Set `ONTOLOGY_WRITEBACK_RECEIVERS_FILE` on the Web service to a read-only, operator-managed JSON file, readable by the Web process. Each entry authorizes one receiver for one exact project UUID. Leave the setting unset to configure zero destinations. Use distinct receiver keys per project and keep each key bound to the same business system. Load the real secret from the deployment's secret store and restrict access to the mounted file.

```json
[{
  "projectId": "11111111-1111-4111-8111-111111111111",
  "key": "order-system",
  "name": "Order system",
  "endpoint": "https://receiver.example.org/ontology/deliveries",
  "secret": "<replace-with-at-least-32-random-characters>"
}]
```

Endpoints use HTTPS. An HTTP endpoint is supported on the literal loopback addresses for an isolated receiver. The sender disables redirects and environment proxies. Hosts, credentials and signing secrets are kept on the server. The file is read for each request.

## Receive and acknowledge

The POST body is UTF-8 JSON with `version`, `projectId`, `transactionId`, `records` and `relationships`. Each affected record occurs once and contains its final revision and currently readable properties. A deletion sends its record and definition identifiers, revision and `deleted: true`. Relationship changes identify the operation, type, endpoints and immutable edge. The transaction name and historical field changes are excluded.

Validate the exact request bytes using the shared secret and the following headers.

1. Read the decimal Unix timestamp in `X-Ontology-Timestamp` and enforce an appropriate clock-skew allowance, such as 120 seconds.
2. Compute SHA-256 of the raw body and compare its lowercase hexadecimal form with `X-Ontology-SHA256`.
3. Take the UUID from `Idempotency-Key`. Compute HMAC-SHA256 over the UTF-8 bytes of `v1.<timestamp>.<delivery-uuid>.<body-sha256>`. Compare `v1=` followed by the lowercase hex HMAC against `X-Ontology-Signature` in constant time. Verify the project matches the receiver's configured scope.
4. Persist the business effect and receipt atomically, with a unique constraint on the delivery UUID. A duplicate UUID with the same digest returns the original receipt. Reject a duplicate with a different digest.
5. After persistence, return HTTP 200 or 201 with a JSON object containing `deliveryId`, `payloadSha256` and a durable UUID `receiptId`. The acknowledgement must fit within 4096 bytes.

The sender persists its delivery UUID and body digest before making the request. A timeout can occur after the receiver committed. **Retry delivery** resends the same logical delivery, rechecks access and current data, and requires the same destination and projected payload. A confirmed delivery returns its existing receipt. A receiver without transactional deduplication cannot safely support retries. The Web process uses an eight-second outer timeout per attempt.

The isolated acceptance receiver and browser exercise are in `web-panel/tests/ontology/writeback_receiver.py` and `web-panel/tests/ontology/writeback.py`.
