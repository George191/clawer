"""Regression: source detail disclosures and diagnostics must not move the surrounding layout.

Run against a loopback Vite frontend: python tests/source_details_layout.py http://127.0.0.1:3107 --channel msedge
"""

import argparse
from ipaddress import ip_address

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("base")
parser.add_argument("--channel", default=None)
args = parser.parse_args()
from playwright.sync_api import sync_playwright, expect
from urllib.parse import urlsplit

assert ip_address(
    urlsplit(args.base).hostname
).is_loopback, "Expected loopback frontend"
source = dict(
    id="fixture",
    name="Fixture source",
    engine="postgresql",
    config=dict(
        host="192.168.1.38",
        port=5432,
        database="www.astral.storage",
        username="reader",
        auth="password",
        tls=False,
    ),
    credentialsConfigured=True,
    catalogAvailable=True,
    updatedAt="2026-11-25T01:00:00Z",
    testedAt="2026-11-25T01:00:00Z",
    capabilities={
        k: (
            dict(status="unavailable", reason="mode_disabled")
            if k not in ["connectionTest", "catalog"]
            else dict(status="available_to_try")
        )
        for k in [
            "connectionTest",
            "catalog",
            "snapshot",
            "committedBatch",
            "reviewedRange",
        ]
    },
)
connector = dict(
    engine="postgresql",
    name="PostgreSQL",
    port=5432,
    category="relational",
    driverAvailable=True,
    catalogAvailable=True,
    cdcSupported=True,
    captureKind="cdc",
)
runtime = dict(
    checkedAt="2026-11-26T09:00:00Z",
    outputMode="debezium",
    connect=dict(
        state="available",
        connectors=[
            dict(
                name="spider-crawler-postgres-cdc-dw_etl",
                state="RUNNING",
                taskStates=["RUNNING"],
                connectionId="fixture",
            )
        ],
    ),
    postgres=dict(
        state="available",
        targets=[dict(connectionId="fixture", state="available")],
        slots=[
            dict(
                name="spider_cdc_8f8bb4b15987e65b", active=True, connectionId="fixture"
            )
        ],
    ),
    kafka=dict(
        state="available",
        consumers=[
            dict(group=k, lag=i * 100, partitions=35)
            for i, k in enumerate(
                [
                    "spider-mongo-cdc-syncer-e4305333acae58dd",
                    "etl-ods-group",
                    "etl-rds-group",
                    "spider-crawler-postgres-cdc-relay",
                    "spider-mongo-cdc-downloader-e4305333acae58dd",
                ]
            )
        ],
    ),
)
GEOMETRY = """() => Object.fromEntries(['.ontology-connections-modal .ui-modal-content','.ontology-connections-detail > header','.source-detail-navigation','.source-layer-bindings','.source-detail-start','.ontology-connections-summary','.source-detail-advanced','.ontology-connections-modal .ui-modal-footer'].map(s=>{const r=document.querySelector(s).getBoundingClientRect();return [s,[r.x,r.y,r.width,r.height]]}))"""
with sync_playwright() as p:
    b = p.chromium.launch(channel=args.channel, args=["--no-proxy-server"])
    for width, height, identity_state in [
        (w, h, s)
        for w, h in [(1647, 1220), (1440, 900), (1366, 768)]
        for s in ["disabled", "unverified"]
    ]:
        page = b.new_page(viewport=dict(width=width, height=height))
        page.set_default_timeout(10000)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(
            'window.__APP_CONFIG__={backendDomainSuffix:"",frontendDomainSuffix:"",httpsPort:""}'
        )

        def route(r):
            path = urlsplit(r.request.url).path
            data = {"available": False}
            if path.endswith("/users/me/context"):
                data = {"user": {"is_superuser": True}}
            elif path.endswith("/etl/connector-types"):
                data = [connector]
            elif path.endswith("/etl/data-connections"):
                data = [source]
            elif path.endswith("/layer-bindings"):
                data = [
                    dict(
                        layer=k,
                        connectionId="fixture",
                        name="Fixture source",
                        executionMode="postgres_worker",
                    )
                    for k in ["ods", "rds", "task"]
                ]
            elif "identity" in path:
                data = dict(state=identity_state, sourceUpdatedAt=None, verifiedAt=None)
            elif path.endswith("/cdc/status"):
                data = runtime
            r.fulfill(json=data)

        page.route("**/api/**", route)
        page.goto(args.base.rstrip("/") + "/tests/fixtures/connections-refresh.html")
        page.get_by_role("button", name="Fixture source", exact=False).first.click()
        page.get_by_role("combobox", name="Logical layer", exact=True).click()
        page.get_by_role("option", name="ods", exact=True).click()
        control = page.get_by_role(
            "combobox", name="Logical layer", exact=True
        ).bounding_box()
        button = page.get_by_role(
            "button", name="Bind to this connection", exact=True
        ).bounding_box()
        assert (
            abs(
                control["y"]
                + control["height"] / 2
                - button["y"]
                - button["height"] / 2
            )
            < 1
        ), (control, button)
        before = page.evaluate(GEOMETRY)
        page.locator(".source-detail-advanced > summary").click()
        for label in ["Live CDC and Kafka", "Feature availability", "Source identity"]:
            page.get_by_role("tab", name=label, exact=True).click()
            if label == "Source identity":
                expect(
                    page.get_by_text(
                        "Source identity verification is not enabled for this connection.",
                        exact=True,
                    )
                    if identity_state == "disabled"
                    else page.get_by_role("region", name="Source identity", exact=True)
                ).to_be_visible()
            after = page.evaluate(GEOMETRY)
            assert before == after, (label, before, after)
            dims = page.locator(".is-overview").evaluate(
                "(e)=>[e.clientHeight,e.scrollHeight,e.clientWidth,e.scrollWidth]"
            )
            pane = page.locator(
                ".source-detail-diagnostics .ui-tabs-tabpane-active"
            ).bounding_box()
            advanced = page.locator(".source-detail-advanced").bounding_box()
            print(
                width,
                height,
                label,
                dims,
                "panel bottom",
                pane["y"] + pane["height"],
                "area bottom",
                advanced["y"] + advanced["height"],
                flush=True,
            )
            assert dims[1] <= dims[0] + 1 and dims[3] <= dims[2] + 1, dims
            assert (
                pane["y"] + pane["height"] <= advanced["y"] + advanced["height"] + 1
            ), (pane, advanced)
        page.get_by_role("tab", name="Live CDC and Kafka", exact=True).click()
        page.get_by_role(
            "button", name="Kafka consumer lag · cluster-wide", exact=True
        ).click()
        dialog = page.get_by_role(
            "dialog", name="Kafka consumer lag · cluster-wide", exact=True
        )
        expect(dialog).to_be_visible()
        expect(dialog.locator("tbody tr")).to_have_count(5)
        assert before == page.evaluate(GEOMETRY)
        dialog.get_by_role("button", name="Done", exact=True).click()
        page.locator(".source-detail-advanced > summary").click()
        assert before == page.evaluate(GEOMETRY)
        assert not errors, errors
        page.close()
    b.close()
