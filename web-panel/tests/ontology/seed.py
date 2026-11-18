"""Seed independent QA principals, published types and controlled records through the candidate HTTP API."""

import json, os, secrets
from pathlib import Path
import httpx
from app.web.core.config import settings

base = "http://127.0.0.1"
assert settings.PROJECT_NAME == "Ontology capability QA" and "ontology_workflow_regression" in str(
    settings.SQLALCHEMY_RUNNABLE_DATABASE_URI
)
with httpx.Client(base_url=base, trust_env=False, timeout=22) as cli:

    def call(method, path, status=200, **kwargs):
        resp = cli.request(method, path, **kwargs)
        if resp.status_code != status:
            raise RuntimeError(f"{method} {path} -> {resp.status_code}")
        return resp.json() if resp.content else None

    creds = {"email": settings.FIRST_SUPERUSER, "password": settings.FIRST_SUPERUSER_PASSWORD}
    auth = call(
        "POST",
        "/api/v1/login/token",
        data={"username": creds["email"], "password": creds["password"]},
    )
    cli.headers["Authorization"] = "Bearer " + auth["access_token"]
    owner = call("POST", "/api/v1/login/test-token")
    creds.update(id=owner["id"], name=owner.get("full_name") or owner["email"])
    org = call("POST", "/api/v1/tenant/", json={"name": "Ontology QA"})
    team = call(
        "POST", "/api/v1/team/", params={"tenant_id": org["id"]}, json={"name": "Acceptance"}
    )
    proj = team["id"]
    sem = "/api/ontology/v1"
    call("POST", sem + "/projects", 201, json={"name": "Capability QA", "teamId": proj})
    reader = {
        "email": "qa-reviewer@example.org",
        "password": secrets.token_urlsafe(24),
        "name": "QA Reviewer",
    }
    profile = call(
        "POST",
        "/api/v1/users/",
        params={"team_id": proj},
        json={
            "email": reader["email"],
            "password": reader["password"],
            "full_name": reader["name"],
            "phone": "10000000002",
        },
    )
    reader["id"] = profile["id"]
    group = {"kind": "group", "id": proj}
    admin = {"kind": "user", "id": owner["id"]}

    def publish(body):
        draft = call("POST", sem + "/drafts", 201, json=body)
        call("POST", sem + "/drafts/" + draft["id"] + "/publish", json={"revision": draft["revision"]})
        return draft["id"]

    def define(key, name):
        fields = ["id", "name", "amount", "category", "owner"]
        body = {
            "id": key,
            "name": name,
            "pluralName": name + "s",
            "projectId": proj,
            "source": {"mode": "none"},
            "primaryKey": ["id"],
            "titleKey": "name",
            "properties": [
                {
                    "id": f,
                    "name": f.capitalize(),
                    "type": "number" if f == "amount" else "string",
                    "required": f in ["id", "name"],
                }
                for f in fields
            ],
            "security": {"mode": "explicit", "objectReaders": [group], "fieldReaders": {}},
            "actions": [
                {"kind": k, "fields": fs, "submitters": [admin]}
                for k, fs in [("create", fields), ("modify", fields[1:]), ("delete", [])]
            ],
        }
        definition_id = publish(body)
        records = []
        for i in range(3):
            values = {
                "id": key + "-" + str(i + 1),
                "name": name + " " + str(i + 1),
                "amount": [10, 25, 40][i],
                "category": ["alpha", "alpha", "beta"][i],
                "owner": reader["id"] if i == 0 else owner["id"],
            }
            r = call(
                "POST",
                sem + "/object-types/" + definition_id + "/actions/create",
                json={"values": values},
            )
            records.append(
                {
                    "id": r["id"],
                    "title": values["name"],
                    "properties": values,
                    "revision": r["revision"],
                }
            )
        return dict(
            id=definition_id,
            name=name,
            records=records,
            numericField="amount",
            groupField="category",
            filterField="category",
        )

    def define_sdk():
        semantic_id = "qa-sdk"
        name = "QA SDK Isolated Type"
        fields = ["id", "title", "count", "visibility", "private-note"]
        definition_id = publish({
            "projectId": proj, "id": semantic_id, "name": name, "pluralName": name + "s",
            "source": {"mode": "none"}, "primaryKey": ["id"], "titleKey": "title",
            "properties": [{"id": field, "name": field, "type": "integer" if field == "count" else "string",
                            "required": field != "private-note"} for field in fields],
            "actions": [{"kind": kind, "fields": editable, "submitters": [admin]}
                        for kind, editable in (("create", fields), ("modify", ["count"]), ("delete", []))],
            "functions": [{"key": "set_count", "name": "Set count", "assignments": [
                {"field": "count", "operation": "set", "operands": [{"parameter": "count"}]},
            ]}],
            "security": {"mode": "explicit", "objectReaders": [group], "fieldReaders": {"private-note": [admin]},
                         "recordRules": [
                             {"kind": "property", "field": "visibility", "match": "equals", "value": state, "readers": readers}
                             for state, readers in (("public", [group]), ("private", [admin]))
                         ]},
        })
        records = []
        for state in ("public", "private"):
            record = call("POST", sem + "/object-types/" + definition_id + "/actions/create", json={"values": {
                "id": state, "title": state + " row", "visibility": state, "count": 3, "private-note": "controlled test fixture",
            }})
            records.append(record["id"])
        return {"id": definition_id, "semantic_id": semantic_id, "name": name,
                "visible_record": records[0], "denied_record": records[1]}

    fixture = {
        "base": base,
        "owner": creds,
        "reader": reader,
        "projectId": proj,
        "source": define("work-order", "QA Work Order"),
        "target": define("equipment", "QA Equipment"),
        "sdk": define_sdk(),
    }
    path = Path("/tmp/ontology-fixture.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(fixture, file)
    print(json.dumps({"ok": True, "principals": 2, "types": 3, "records": 8}))
