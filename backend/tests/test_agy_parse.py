"""Tests unitaires des fonctions pures ``parse_ligne`` pour ``agy`` et ``claude``."""

from backend.llm.agy_engine import parse_ligne as parse_agy
from backend.llm.claude_engine import parse_ligne as parse_claude


def test_parse_flux_agy_stream_json() -> None:
    """Valide le parsing d'un flux stream-json complet conforme à ARCHITECTURE §1."""
    flux = [
        "",
        "Ligne parasite non JSON",
        '{"event":"init","conversation_id":"conv-agy-001"}',
        '{"event":"step_update","step_update":{"step_type":"thinking","state":"ACTIVE","text_delta":"hmm"}}',
        '{"event":"step_update","step_update":{"step_type":"agent_response","state":"ACTIVE","text_delta":"Bon"}}',
        '{"event":"step_update","step_update":{"step_type":"agent_response","state":"DONE","text_delta":"jour"}}',
        (
            '{"event":"result","result":{"conversation_id":"conv-agy-001","status":"SUCCESS",'
            '"response":"Bonjour","error":"","usage":{"input_tokens":20100,"output_tokens":42}}}'
        ),
    ]

    evenements = [evt for ligne in flux if (evt := parse_agy(ligne)) is not None]
    assert evenements == [
        {"type": "init", "conversation_id": "conv-agy-001"},
        {"type": "delta", "delta": "Bon", "state": "ACTIVE"},
        {"type": "delta", "delta": "jour", "state": "DONE"},
        {
            "type": "result",
            "status": "SUCCESS",
            "conversation_id": "conv-agy-001",
            "response": "Bonjour",
            "error": "",
            "input_tokens": 20100,
            "output_tokens": 42,
        },
    ]


def test_parse_flux_claude_stream_json() -> None:
    """Valide le parsing d'un flux stream-json émis par Claude Code CLI."""
    flux = [
        '{"type":"system","subtype":"init","session_id":"sess-cl-99"}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Sa"}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"lut"}}}',
        (
            '{"type":"result","result":"Salut","session_id":"sess-cl-99","is_error":false,'
            '"usage":{"input_tokens":150,"output_tokens":12}}'
        ),
    ]

    evenements = [evt for ligne in flux if (evt := parse_claude(ligne)) is not None]
    assert evenements == [
        {"type": "init", "session_id": "sess-cl-99"},
        {"type": "delta", "delta": "Sa"},
        {"type": "delta", "delta": "lut"},
        {
            "type": "result",
            "result": "Salut",
            "session_id": "sess-cl-99",
            "is_error": False,
            "input_tokens": 150,
            "output_tokens": 12,
        },
    ]
