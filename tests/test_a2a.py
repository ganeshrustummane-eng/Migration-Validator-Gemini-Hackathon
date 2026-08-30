"""
Tests for the A2A (Agent2Agent) protocol adapter.

Exercises handle_a2a_request as a pure function against a stub run_chat
callback — no HTTP, no GeminiAgent, no network. This verifies the JSON-RPC
envelope handling independent of whether our guess at Gemini Enterprise's
exact wire format is correct.
"""
from gemini_connector.a2a import handle_a2a_request, load_agent_card


def _stub_chat(text):
    return {"text": f"echo: {text}"}


def test_message_send_returns_agent_reply():
    request = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "hello"}]}},
    }
    response = handle_a2a_request(request, _stub_chat)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "1"
    assert response["result"]["role"] == "agent"
    assert response["result"]["parts"][0]["text"] == "echo: hello"


def test_message_send_accepts_legacy_type_discriminator():
    request = {
        "jsonrpc": "2.0",
        "id": "2",
        "method": "message/send",
        "params": {"message": {"parts": [{"type": "text", "text": "legacy"}]}},
    }
    response = handle_a2a_request(request, _stub_chat)
    assert response["result"]["parts"][0]["text"] == "echo: legacy"


def test_tasks_send_alias_accepted():
    request = {
        "jsonrpc": "2.0",
        "id": "3",
        "method": "tasks/send",
        "params": {"message": {"parts": [{"kind": "text", "text": "hi"}]}},
    }
    response = handle_a2a_request(request, _stub_chat)
    assert "result" in response


def test_missing_jsonrpc_envelope_is_invalid_request():
    response = handle_a2a_request({"method": "message/send"}, _stub_chat)
    assert response["error"]["code"] == -32600


def test_unknown_method_is_method_not_found():
    request = {"jsonrpc": "2.0", "id": "4", "method": "tasks/cancel", "params": {}}
    response = handle_a2a_request(request, _stub_chat)
    assert response["error"]["code"] == -32601


def test_empty_message_text_is_invalid_params():
    request = {
        "jsonrpc": "2.0",
        "id": "5",
        "method": "message/send",
        "params": {"message": {"parts": []}},
    }
    response = handle_a2a_request(request, _stub_chat)
    assert response["error"]["code"] == -32602


def test_load_agent_card_returns_dict_with_skills():
    card = load_agent_card()
    assert "name" in card
    assert isinstance(card.get("skills"), list)
