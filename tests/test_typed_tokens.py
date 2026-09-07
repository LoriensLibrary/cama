"""Tests for cama.memory.typed_tokens and its wiring into DyadAgent retrieval.

Two layers:
  1. The encoder/decoder in isolation (pure, no deps).
  2. The flag actually switching the retrieval format inside a real
     DyadAgent._assemble_system_prompt on an isolated temp vault.
"""
from __future__ import annotations

import pytest

from cama.agents import cama_agent, cama_dyad
from cama.agents.cama_agent_backends import EchoBackend
from cama.memory.typed_tokens import (
    SPECIAL_TOKENS,
    encode_memory,
    parse_typed_tokens,
)


@pytest.fixture(autouse=True)
def isolated_vault(tmp_path, monkeypatch):
    monkeypatch.setattr(cama_dyad, "VAULT_ROOT", tmp_path / "vaults")
    yield


SAMPLE = {
    "memory_type": "identity",
    "source_type": "teaching",
    "proposed_by": "user",
    "status": "durable",
    "is_core": 1,
    "trust_score": 0.95,
    "context": "Conversation: x | People: angela, aelen",
    "raw_text": "Angela built CAMA on the worst day.",
    "affect": {
        "valence": 0.8,
        "arousal": 0.4,
        "emotions": {"determination": 0.9, "pride": 0.7, "love": 0.2},
    },
}


# ------------------------------------------------------------------
# Encoder, in isolation
# ------------------------------------------------------------------

def test_encode_contains_provenance_and_affect():
    s = encode_memory(SAMPLE)
    for tok in (
        "<MEM>", "<KIND:identity>", "<SRC:teaching>", "<BY:user>",
        "<DURABLE>", "<CORE>", "<TRUST:hi>", "<VAL:+2>",
        "<EMO:determination:hi>", "<TXT>", "</MEM>",
    ):
        assert tok in s, f"missing token: {tok}"
    assert "<WHO:angela>" in s  # pulled from context "People: ..."
    assert "worst day" in s     # text preserved verbatim


def test_weak_emotions_below_floor_are_dropped():
    # love at 0.2 is below the 0.4 floor -> no token.
    assert "<EMO:love" not in encode_memory(SAMPLE)


def test_round_trip_recovers_structured_fields():
    p = parse_typed_tokens(encode_memory(SAMPLE))
    assert p["memory_type"] == "identity"
    assert p["source_type"] == "teaching"
    assert p["proposed_by"] == "user"
    assert p["status"] == "durable"
    assert p["is_core"] is True
    assert p["trust"] == "hi"
    assert "angela" in p["people"]
    assert p["text"] == SAMPLE["raw_text"]
    assert ("determination", "hi") in p["emotions"]


def test_provisional_and_missing_fields_tolerated():
    s = encode_memory({"raw_text": "bare", "status": "provisional"})
    assert "<PROVISIONAL>" in s
    assert "<DURABLE>" not in s
    assert "<TXT> bare </TXT>" in s


def test_valence_buckets_span_the_range():
    neg = encode_memory({"raw_text": "x", "affect": {"valence": -0.9}})
    zero = encode_memory({"raw_text": "x", "affect": {"valence": 0.0}})
    pos = encode_memory({"raw_text": "x", "affect": {"valence": 0.9}})
    assert "<VAL:-2>" in neg
    assert "<VAL:0>" in zero
    assert "<VAL:+2>" in pos


def test_special_tokens_unique():
    assert len(SPECIAL_TOKENS) == len(set(SPECIAL_TOKENS))


# ------------------------------------------------------------------
# Integration: the flag switches the live retrieval format
# ------------------------------------------------------------------

def _retrieved_record():
    return {
        "id": 1,
        "memory_type": "experience",
        "source_type": "teaching",
        "proposed_by": "user",
        "status": "durable",
        "is_core": 1,
        "trust_score": 0.9,
        "text": "she shipped it",
        "raw_text": "she shipped it",
        "affect": {"valence": 0.7, "arousal": 0.5, "emotions": {"pride": 0.8}},
    }


def _agent(typed_tokens: bool):
    r = cama_dyad.init_dyad(person_name="Jordan", ai_name="Aurora")
    return cama_agent.DyadAgent(
        r["dyad_id"], EchoBackend(), typed_tokens=typed_tokens
    )


def _system_prompt_with_retrieved(agent):
    return agent._assemble_system_prompt(
        identity=[],
        recent_teachings=[],
        last_journal=None,
        retrieved=[_retrieved_record()],
        counterweights=[],
        persona_info=None,
    )


def test_flag_true_emits_typed_tokens():
    sp = _system_prompt_with_retrieved(_agent(typed_tokens=True))
    assert "<MEM>" in sp
    assert "<TRUST:hi>" in sp
    assert "<EMO:pride:hi>" in sp


def test_flag_false_keeps_legacy_format():
    sp = _system_prompt_with_retrieved(_agent(typed_tokens=False))
    assert "<MEM>" not in sp
    assert "[experience] she shipped it" in sp


def test_default_is_typed_tokens_on():
    r = cama_dyad.init_dyad(person_name="Jordan", ai_name="Aurora")
    agent = cama_agent.DyadAgent(r["dyad_id"], EchoBackend())
    assert agent.typed_tokens is True
