"""§28 P7 instruction 7: JSON Schema contract tests for the agent
envelope and all seven §14 message types. Pure -- no database, no HTTP
server. Instances are built from the real domain/application code (real
signing, real handler functions) wherever that's possible without I/O,
matching tests/contract/test_protocol_schemas.py's own established
precedent (§28 P4 instruction 6).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from actl.application.agents import merchant
from actl.domain.agent.envelope import AgentEnvelope, sign_envelope_ed25519
from actl.domain.policy.reason_codes import ReasonCode
from actl.interfaces.agent.schemas import (
    CapabilityDiscoverBody,
    CatalogQueryBody,
    OrderProposeBody,
    OrderStatusBody,
    ReceiptIssueBody,
)
from actl.interfaces.http.routers.well_known import _DOCUMENT as WELL_KNOWN_DOCUMENT

_PROTOCOL_DIR = Path(__file__).resolve().parents[2] / "docs" / "protocol"


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((_PROTOCOL_DIR / name).read_text())


def _example_envelope() -> AgentEnvelope:
    draft = AgentEnvelope.model_validate(
        {
            "protocol": "actl.acp/1",
            "msg_id": "msg_01JX8Z71F",
            "ts": datetime(2026, 8, 28, 9, 4, 9, 881000, tzinfo=UTC),
            "from": "agt_buyer_01",
            "to": "agt_merchant_01",
            "corr_id": "01JX8Z7C1M4RQ",
            "type": "capability.discover",
            "body": {"supported_protocols": ["actl.acp/1"]},
        }
    )
    private_key = Ed25519PrivateKey.generate()
    return sign_envelope_ed25519(draft, private_key, "ed25519:9f31c2")


def test_envelope_validates_against_schema() -> None:
    schema = _load_schema("agent-envelope.schema.json")
    body = _example_envelope().model_dump(mode="json", by_alias=True)
    jsonschema.validate(body, schema)


def test_envelope_rejects_an_extra_field_at_the_schema_level() -> None:
    schema = _load_schema("agent-envelope.schema.json")
    body = _example_envelope().model_dump(mode="json", by_alias=True)
    body["extra_field"] = "not part of the protocol"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, schema)


def test_message_error_body_validates_against_schema() -> None:
    schema = _load_schema("message-error.schema.json")
    error = merchant.HandlerError(ReasonCode.MANDATE_INVALID, "no such mandate", retryable=False)
    body = {
        "reason_code": str(error.reason_code),
        "message": error.message,
        "retryable": error.retryable,
    }
    jsonschema.validate(body, schema)


def test_capability_discover_request_validates_against_schema() -> None:
    schema = _load_schema("capability_discover_request.schema.json")
    jsonschema.validate({"supported_protocols": ["actl.acp/1"]}, schema)
    CapabilityDiscoverBody.model_validate({"supported_protocols": ["actl.acp/1"]})

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"supported_protocols": []}, schema)


def test_capability_discover_response_reuses_the_well_known_document_schema() -> None:
    """capability.discover's response body is exactly the same document
    GET /.well-known/agent-commerce.json advertises -- one schema, one
    source of truth, no drift possible between the two."""
    schema = _load_schema("agent-commerce.schema.json")
    jsonschema.validate(WELL_KNOWN_DOCUMENT, schema)


def test_catalog_query_request_validates_against_schema() -> None:
    schema = _load_schema("catalog_query_request.schema.json")
    body = CatalogQueryBody.model_validate(
        {"category": "travel.hotel", "location": "Goa,IN", "max_unit_minor": 300000}
    ).model_dump(mode="json")
    jsonschema.validate(body, schema)


def test_order_propose_request_validates_against_schema() -> None:
    schema = _load_schema("order_propose_request.schema.json")
    body = OrderProposeBody.model_validate(
        {
            "quote_id": "qte_01JX8Z70A",
            "quote_hash": "sha256:" + "9" * 64,
            "mandate_id": "mdt_01JX8Z6QK4T2N9V0",
            "mandate_spec_hash": "sha256:" + "6" * 64,
            "intent_hash": "sha256:" + "4" * 64,
        }
    ).model_dump(mode="json")
    jsonschema.validate(body, schema)

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({k: v for k, v in body.items() if k != "intent_hash"}, schema)


def test_order_propose_response_validates_against_schema_both_branches() -> None:
    schema = _load_schema("order_propose_response.schema.json")

    accept = merchant.HandlerResult(
        type="order.propose", body={"decision": "accept", "order_id": "ord_x", "saga_id": "ik_x"}
    )
    jsonschema.validate(accept.body, schema)

    reject = merchant._reject("trc_x", ReasonCode.MANDATE_INVALID)
    jsonschema.validate(reject.body, schema)


def test_order_status_request_validates_against_schema() -> None:
    schema = _load_schema("order_status_request.schema.json")
    body = OrderStatusBody.model_validate({"order_id": "ord_x"}).model_dump(mode="json")
    jsonschema.validate(body, schema)


def test_order_status_response_validates_against_schema() -> None:
    schema = _load_schema("order_status_response.schema.json")
    body = {
        "order_id": "ord_x",
        "status": "CAPTURED",
        "amount_minor": 840000,
        "currency": "INR",
        "provider_payment_id": "pay_x",
        "audit_seq_from": 41,
        "audit_seq_to": 48,
    }
    jsonschema.validate(body, schema)


def test_receipt_issue_request_validates_against_schema() -> None:
    schema = _load_schema("receipt_issue_request.schema.json")
    body = ReceiptIssueBody.model_validate({"order_id": "ord_x"}).model_dump(mode="json")
    jsonschema.validate(body, schema)


def test_receipt_issue_response_validates_against_schema() -> None:
    schema = _load_schema("receipt_issue_response.schema.json")
    body = {
        "order_id": "ord_x",
        "payment_id": "pay_x",
        "amount_minor": 840000,
        "currency": "INR",
        "audit_seq_from": 41,
        "audit_seq_to": 48,
    }
    jsonschema.validate(body, schema)


def test_capability_discover_response_advertises_ed25519() -> None:
    """§28 P7: Ed25519 is now live via the agent identity registry --
    the capability document must say so, not just still list the
    HMAC-SHA256 development fallback."""
    assert "Ed25519" in WELL_KNOWN_DOCUMENT["signing"]["algorithms"]
    assert WELL_KNOWN_DOCUMENT["endpoints"]["messages"] == "/agent/v1/messages"
