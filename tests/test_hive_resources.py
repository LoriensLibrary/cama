"""Tests for cama_hive_resources -- the domain expertise layer (Kalos-style).

Properties under test:
  1. Publishing creates an immutable versioned directory with fingerprints.
  2. Republishing the same version is rejected.
  3. Install requires consent.hive_consume; refused otherwise.
  4. Install records appear in the dyad's installed.json with provenance.
  5. Verify catches content tampering after publish.
  6. Two dyads installing different versions stay independent.
  7. Agent runtime surfaces installed resources in the system prompt.
  8. Uninstall is real; agent stops seeing the resource after uninstall.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cama.agents import cama_dyad
from cama.hive import cama_hive_protocol as hp
from cama.hive import cama_hive_resources as hr
from cama.agents import cama_agent
from cama.agents.cama_agent_backends import EchoBackend


@pytest.fixture(autouse=True)
def isolated_vaults_and_hive(tmp_path, monkeypatch):
    monkeypatch.setattr(cama_dyad, "VAULT_ROOT", tmp_path / "vaults")
    monkeypatch.setattr(hp, "HIVE_ROOT", tmp_path / "hive")
    yield


def _make_resource_content(root: Path, *, kind: str = "lora") -> Path:
    """Create a tiny fake resource content directory."""
    d = root / "fake_content"
    d.mkdir(parents=True, exist_ok=True)
    if kind == "lora":
        (d / "adapter_config.json").write_text(json.dumps({
            "lora_r": 8, "lora_alpha": 16,
        }))
        (d / "adapter_model.bin").write_bytes(b"\x00" * 128)  # stub weights
    elif kind == "knowledge":
        (d / "knowledge.jsonl").write_text(
            json.dumps({"excerpt": "Motivational interviewing: ask open questions."}) + "\n" +
            json.dumps({"excerpt": "Behavior change: anchor on values, not goals."}) + "\n"
        )
        (d / "citations.txt").write_text("Miller, W. R., & Rollnick, S. (2013).\n")
    return d


def _opt_in_consume(dyad_id: str):
    cama_dyad.update_consent(dyad_id, {"hive_consume": True})


# ============================================================
# Publishing
# ============================================================

def test_publish_creates_versioned_immutable_artifact(tmp_path):
    src = _make_resource_content(tmp_path, kind="lora")
    manifest = hr.publish_resource(
        name="kalos_coaching",
        version="v1",
        resource_type="domain_lora",
        publisher="kalos.health",
        content_source=src,
        description="Stub coaching LoRA",
        license="proprietary-trial",
    )
    assert manifest["name"] == "kalos_coaching"
    assert manifest["version"] == "v1"
    assert manifest["publisher"] == "kalos.health"
    assert manifest["content_sha256"]
    # The version directory was created with content.
    vdir = hr._resource_version_dir("kalos_coaching", "v1")
    assert (vdir / "manifest.json").exists()
    assert (vdir / "content" / "adapter_model.bin").exists()
    assert (vdir / "content.sha256").exists()


def test_republishing_same_version_is_rejected(tmp_path):
    src = _make_resource_content(tmp_path)
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src,
    )
    with pytest.raises(FileExistsError):
        hr.publish_resource(
            name="kalos_coaching", version="v1",
            resource_type="domain_lora", publisher="kalos.health",
            content_source=src,
        )


def test_publish_rejects_unknown_type(tmp_path):
    src = _make_resource_content(tmp_path)
    with pytest.raises(ValueError):
        hr.publish_resource(
            name="x", version="v1", resource_type="oracle",
            publisher="kalos.health", content_source=src,
        )


def test_latest_marker_is_set_on_publish(tmp_path):
    src = _make_resource_content(tmp_path)
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src,
    )
    listing = hr.list_resources()
    by_name = {r["name"]: r for r in listing}
    assert by_name["kalos_coaching"]["latest_version"] == "v1"


# ============================================================
# Verify
# ============================================================

def test_verify_passes_for_intact_resource(tmp_path):
    src = _make_resource_content(tmp_path)
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src,
    )
    v = hr.verify_resource("kalos_coaching", "v1")
    assert v["ok"] is True


def test_verify_catches_tampering(tmp_path):
    src = _make_resource_content(tmp_path)
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src,
    )
    # Tamper with the content after publish.
    vdir = hr._resource_version_dir("kalos_coaching", "v1")
    with (vdir / "content" / "adapter_model.bin").open("ab") as f:
        f.write(b"INJECTED")
    v = hr.verify_resource("kalos_coaching", "v1")
    assert v["ok"] is False
    assert v["reason"] == "content_sha256_mismatch"


# ============================================================
# Install consent gating
# ============================================================

def test_install_refused_without_consent(tmp_path):
    src = _make_resource_content(tmp_path)
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src,
    )
    r = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    res = hr.install_resource(r["dyad_id"], "kalos_coaching")
    assert res["status"] == "refused"
    assert "hive_consume" in res["reason"]
    # No installed.json was created.
    assert hr.list_installed(r["dyad_id"]) == []


def test_install_succeeds_with_consent(tmp_path):
    src = _make_resource_content(tmp_path)
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src,
    )
    r = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    _opt_in_consume(r["dyad_id"])
    res = hr.install_resource(r["dyad_id"], "kalos_coaching")
    assert res["status"] == "installed"
    assert res["version"] == "v1"
    assert res["resource_type"] == "domain_lora"


def test_install_records_provenance(tmp_path):
    src = _make_resource_content(tmp_path)
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src,
    )
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_consume(r["dyad_id"])
    hr.install_resource(r["dyad_id"], "kalos_coaching")
    installed = hr.list_installed(r["dyad_id"])
    assert len(installed) == 1
    entry = installed[0]
    assert entry["publisher"] == "kalos.health"
    assert entry["content_sha256"]
    assert "installed_at" in entry


# ============================================================
# Install per-dyad isolation
# ============================================================

def test_two_dyads_install_independently(tmp_path):
    src1 = _make_resource_content(tmp_path / "v1src", kind="lora")
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src1,
    )
    src2 = _make_resource_content(tmp_path / "v2src", kind="lora")
    hr.publish_resource(
        name="kalos_coaching", version="v2",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src2,
    )

    a = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    b = cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")
    _opt_in_consume(a["dyad_id"])
    _opt_in_consume(b["dyad_id"])

    hr.install_resource(a["dyad_id"], "kalos_coaching", version="v1")
    hr.install_resource(b["dyad_id"], "kalos_coaching", version="v2")

    installed_a = hr.list_installed(a["dyad_id"])
    installed_b = hr.list_installed(b["dyad_id"])
    assert installed_a[0]["version"] == "v1"
    assert installed_b[0]["version"] == "v2"


# ============================================================
# Uninstall
# ============================================================

def test_uninstall_is_real(tmp_path):
    src = _make_resource_content(tmp_path)
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src,
    )
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_consume(r["dyad_id"])
    hr.install_resource(r["dyad_id"], "kalos_coaching")
    assert len(hr.list_installed(r["dyad_id"])) == 1
    res = hr.uninstall_resource(r["dyad_id"], "kalos_coaching")
    assert res["status"] == "uninstalled"
    assert hr.list_installed(r["dyad_id"]) == []


def test_uninstall_unknown_is_idempotent(tmp_path):
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    res = hr.uninstall_resource(r["dyad_id"], "nonexistent")
    assert res["status"] == "not_installed"


# ============================================================
# Agent runtime integration
# ============================================================

def test_agent_surfaces_installed_resources_in_system_prompt(tmp_path):
    src = _make_resource_content(tmp_path)
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src,
    )
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_consume(r["dyad_id"])
    hr.install_resource(r["dyad_id"], "kalos_coaching")

    agent = cama_agent.DyadAgent(r["dyad_id"], EchoBackend())
    ctx = agent.boot()
    sp = ctx["system_prompt"]
    assert "Installed domain resources" in sp
    assert "kalos_coaching@v1" in sp
    assert "kalos.health" in sp
    assert ctx["installed_resources"]
    assert ctx["installed_resources"][0]["name"] == "kalos_coaching"


def test_agent_loads_knowledge_excerpts_for_knowledge_indices(tmp_path):
    src = _make_resource_content(tmp_path, kind="knowledge")
    hr.publish_resource(
        name="kalos_knowledge", version="v1",
        resource_type="knowledge_index", publisher="kalos.health",
        content_source=src,
    )
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_consume(r["dyad_id"])
    hr.install_resource(r["dyad_id"], "kalos_knowledge")

    agent = cama_agent.DyadAgent(r["dyad_id"], EchoBackend())
    ctx = agent.boot()
    sp = ctx["system_prompt"]
    assert "Domain knowledge available" in sp
    assert "Motivational interviewing" in sp
    assert any(
        "kalos_knowledge" == k["resource"] for k in ctx["knowledge_excerpts"]
    )


def test_agent_without_installs_does_not_mention_resources(tmp_path):
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    agent = cama_agent.DyadAgent(r["dyad_id"], EchoBackend())
    ctx = agent.boot()
    sp = ctx["system_prompt"]
    assert "Installed domain resources" not in sp
    assert "Domain knowledge available" not in sp
    assert ctx["installed_resources"] == []


def test_uninstall_removes_resource_from_agent_view(tmp_path):
    src = _make_resource_content(tmp_path)
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src,
    )
    r = cama_dyad.init_dyad(person_name="X", ai_name="Y")
    _opt_in_consume(r["dyad_id"])
    hr.install_resource(r["dyad_id"], "kalos_coaching")

    agent = cama_agent.DyadAgent(r["dyad_id"], EchoBackend())
    assert "kalos_coaching" in agent.boot()["system_prompt"]

    hr.uninstall_resource(r["dyad_id"], "kalos_coaching")
    agent2 = cama_agent.DyadAgent(r["dyad_id"], EchoBackend())
    assert "kalos_coaching" not in agent2.boot()["system_prompt"]


# ============================================================
# Cross-dyad isolation extends to agent view
# ============================================================

def test_dyad_a_install_does_not_affect_dyad_b_agent(tmp_path):
    src = _make_resource_content(tmp_path)
    hr.publish_resource(
        name="kalos_coaching", version="v1",
        resource_type="domain_lora", publisher="kalos.health",
        content_source=src,
    )
    a = cama_dyad.init_dyad(person_name="Alice", ai_name="Anya")
    b = cama_dyad.init_dyad(person_name="Bob", ai_name="Brio")
    _opt_in_consume(a["dyad_id"])
    hr.install_resource(a["dyad_id"], "kalos_coaching")

    agent_b = cama_agent.DyadAgent(b["dyad_id"], EchoBackend())
    sp = agent_b.boot()["system_prompt"]
    assert "kalos_coaching" not in sp
