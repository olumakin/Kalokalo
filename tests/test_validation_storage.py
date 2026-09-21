"""
Validation test suite for Stage 1: Epoch Manifest, Provenance Engine, and Append-Only Storage.
"""
from datetime import datetime, timezone
from pathlib import Path
import re

import pytest

from src.config import get_config
from src.tracking.events import (
    DuplicateIdempotencyCollisionError,
    EventType,
    ImmutableEventStore,
    ProspectiveEvent,
    TestFlag,
    compute_idempotency_key,
)
from src.validation.manifest import (
    EXTERNAL_CONTRACT_IDENTITIES,
    EpochManifest,
    create_epoch_manifest,
    get_code_identity,
    get_semantic_config_identity,
    is_working_tree_dirty,
)


def test_semantic_fingerprint_deterministic():
    """Changing dictionary key order in config must produce identical SHA-256 digest."""
    base_cfg = get_config().model_dump()

    # Create two dictionaries with radically different key insertion orders
    order_a = {
        "model": dict(base_cfg["model"]),
        "edge": dict(base_cfg["edge"]),
        "devig": dict(base_cfg["devig"]),
        "leagues": dict(base_cfg["leagues"]),
    }
    order_b = {
        "leagues": dict(reversed(list(base_cfg["leagues"].items()))),
        "devig": dict(base_cfg["devig"]),
        "edge": dict(reversed(list(base_cfg["edge"].items()))),
        "model": dict(reversed(list(base_cfg["model"].items()))),
    }

    hash_a = get_semantic_config_identity(config=order_a)
    hash_b = get_semantic_config_identity(config=order_b)

    assert len(hash_a) == 64
    assert hash_a == hash_b, "Key order altered canonical SHA-256 digest"


def test_semantic_fingerprint_changes_on_semantic_config_change():
    """Any mutation affecting predictive or decision semantics must produce a new hash."""
    base_cfg = get_config().model_dump()
    base_hash = get_semantic_config_identity(config=base_cfg)

    # 1. Mutate decay parameter xi
    cfg_xi = get_config().model_dump()
    cfg_xi["model"]["xi_decay"] = 0.0080
    assert get_semantic_config_identity(config=cfg_xi) != base_hash

    # 2. Mutate eligibility threshold
    cfg_elig = get_config().model_dump()
    cfg_elig["model"]["min_matches_for_team_rating"] = 20
    assert get_semantic_config_identity(config=cfg_elig) != base_hash

    # 3. Mutate EV hurdle
    cfg_ev = get_config().model_dump()
    cfg_ev["edge"]["min_ev"] = 0.05
    assert get_semantic_config_identity(config=cfg_ev) != base_hash

    # 4. Mutate exposure cap
    cfg_cap = get_config().model_dump()
    cfg_cap["edge"]["single_match_cap"] = 0.03
    assert get_semantic_config_identity(config=cfg_cap) != base_hash

    # 5. Mutate team mappings
    hash_with_tm1 = get_semantic_config_identity(config=base_cfg, team_mappings={"Arsenal": "ARS"})
    hash_with_tm2 = get_semantic_config_identity(config=base_cfg, team_mappings={"Arsenal": "AFC"})
    assert hash_with_tm1 != hash_with_tm2


def test_code_identity_binding():
    """get_code_identity must return valid 40-character Git SHA from repo."""
    sha = get_code_identity()
    assert len(sha) == 40
    assert re.match(r"^[0-9a-fA-F]{40}$", sha)


def test_external_contract_identity_binding():
    """External contracts must be explicitly versioned."""
    manifest = create_epoch_manifest(is_test=True)
    assert "price_source_contract" in manifest.external_contracts
    assert manifest.external_contracts["price_source_contract"] == EXTERNAL_CONTRACT_IDENTITIES["price_source_contract"]
    assert "PriceSourceContract::v" in manifest.external_contracts["price_source_contract"]


def test_dirty_tree_detectable():
    """is_working_tree_dirty must return a boolean reflecting working tree modifications."""
    dirty = is_working_tree_dirty()
    assert isinstance(dirty, bool)


def test_event_idempotency():
    """Submitting the exact same event twice must return existing event without duplication."""
    store = ImmutableEventStore()
    ikey = compute_idempotency_key(
        event_type=EventType.PREDICTION_RECORD,
        entity_id="FIX-2026-001",
        timestamp_str="2026-09-21T12:00:00Z",
    )
    event1 = ProspectiveEvent(
        event_type=EventType.PREDICTION_RECORD,
        epoch_id="EPOCH-001-TEST",
        idempotency_key=ikey,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
        payload={"home_team": "ARS", "p_draw": 0.28},
    )

    saved1, inserted1 = store.append(event1)
    assert inserted1 is True

    # Re-submit identical event
    event2 = ProspectiveEvent(
        event_type=EventType.PREDICTION_RECORD,
        epoch_id="EPOCH-001-TEST",
        idempotency_key=ikey,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
        payload={"home_team": "ARS", "p_draw": 0.28},
    )
    saved2, inserted2 = store.append(event2)
    assert inserted2 is False
    assert saved1.event_id == saved2.event_id


def test_distinct_events_not_collapsed():
    """Events with distinct timestamps, entities, or qualifiers must generate distinct keys and store separately."""
    store = ImmutableEventStore()

    k1 = compute_idempotency_key(EventType.MARKET_BENCHMARK_SNAPSHOT, "FIX-1", "2026-09-21T10:00:00Z")
    k2 = compute_idempotency_key(EventType.MARKET_BENCHMARK_SNAPSHOT, "FIX-1", "2026-09-21T10:15:00Z")
    k3 = compute_idempotency_key(EventType.MARKET_BENCHMARK_SNAPSHOT, "FIX-2", "2026-09-21T10:00:00Z")

    assert k1 != k2
    assert k1 != k3

    e1 = ProspectiveEvent(
        event_type=EventType.MARKET_BENCHMARK_SNAPSHOT,
        idempotency_key=k1,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
        payload={"odds_draw": 3.4},
    )
    e2 = ProspectiveEvent(
        event_type=EventType.MARKET_BENCHMARK_SNAPSHOT,
        idempotency_key=k2,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
        payload={"odds_draw": 3.4},  # identical payload, distinct timestamp
    )

    store.append(e1)
    store.append(e2)
    assert store.get_by_key(k1) is not None
    assert store.get_by_key(k2) is not None
    assert store.get_by_key(k1).event_id != store.get_by_key(k2).event_id


def test_amendment_preserves_original():
    """Amendment event must store a new event pointing to target_event_id, leaving original untouched."""
    store = ImmutableEventStore()
    ikey = compute_idempotency_key(EventType.PREDICTION_RECORD, "FIX-1", "2026-09-21T10:00:00Z")
    original = ProspectiveEvent(
        event_type=EventType.PREDICTION_RECORD,
        idempotency_key=ikey,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
        payload={"p_draw": 0.25},
    )
    store.append(original)

    amendment = store.append_amendment(
        target_event_id=original.event_id,
        amendment_type="CORRECTION_ODDS_SOURCE",
        reason="Upstream provider sent late quote fix",
        corrected_payload={"p_draw": 0.27},
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
    )

    # Original is untouched
    orig_reloaded = store.get_by_id(original.event_id)
    assert orig_reloaded.payload["p_draw"] == 0.25

    # Amendment points to original
    assert amendment.target_event_id == original.event_id
    assert amendment.payload["corrected_payload"]["p_draw"] == 0.27


def test_prospective_event_requires_epoch_identity():
    """Prospective events without an epoch_id must be rejected."""
    ikey = compute_idempotency_key(EventType.PREDICTION_RECORD, "FIX-1", "2026-09-21T10:00:00Z")

    # Missing epoch_id for PROSPECTIVE event raises validation error
    with pytest.raises(ValueError, match="require an explicit epoch_id"):
        ProspectiveEvent(
            event_type=EventType.PREDICTION_RECORD,
            epoch_id=None,
            idempotency_key=ikey,
            test_flag=TestFlag.PROSPECTIVE,
        )

    # Test fixture does not require epoch_id
    test_evt = ProspectiveEvent(
        event_type=EventType.PREDICTION_RECORD,
        epoch_id=None,
        idempotency_key=ikey,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
    )
    assert test_evt.test_flag == TestFlag.TEST_ONLY_NON_PROSPECTIVE


def test_test_fixture_cannot_enter_prospective_denominator():
    """Events tagged with TEST_ONLY_NON_PROSPECTIVE must never be returned in prospective listing."""
    store = ImmutableEventStore()

    k_test = compute_idempotency_key(EventType.PREDICTION_RECORD, "FIX-TEST", "2026-09-21T10:00:00Z")
    test_event = ProspectiveEvent(
        event_type=EventType.PREDICTION_RECORD,
        idempotency_key=k_test,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
        payload={"test": True},
    )
    store.append(test_event)

    k_pro = compute_idempotency_key(EventType.PREDICTION_RECORD, "FIX-PRO", "2026-09-21T10:00:00Z")
    pro_event = ProspectiveEvent(
        event_type=EventType.PREDICTION_RECORD,
        epoch_id="EPOCH-001",
        idempotency_key=k_pro,
        test_flag=TestFlag.PROSPECTIVE,
        payload={"test": False},
    )
    store.append(pro_event)

    prospective_list = store.list_prospective_events()
    assert len(prospective_list) == 1
    assert prospective_list[0].event_id == pro_event.event_id
    assert all(e.test_flag == TestFlag.PROSPECTIVE for e in prospective_list)


def test_append_only_schema_contract():
    """Verify supabase/schema.sql contains prospective_events with append-only policies and no UPDATE/DELETE."""
    schema_path = Path(__file__).resolve().parents[1] / "supabase" / "schema.sql"
    content = schema_path.read_text(encoding="utf-8")

    assert "CREATE TABLE prospective_events" in content
    assert "idempotency_key TEXT UNIQUE NOT NULL" in content
    assert "chk_test_flag CHECK" in content
    assert "chk_event_epoch CHECK" in content
    assert "CREATE POLICY \"Allow read prospective_events\" ON prospective_events FOR SELECT" in content
    assert "CREATE POLICY \"Allow write prospective_events\" ON prospective_events FOR INSERT" in content

    # Assert no UPDATE or DELETE policies exist for prospective_events
    assert "FOR UPDATE ON prospective_events" not in content
    assert "FOR DELETE ON prospective_events" not in content


def test_adversarial_checks():
    """Adversarial checks: malformed SHA, placeholder hashes, conflicting idempotency payloads."""
    # 1. Placeholder hash rejection in manifest
    with pytest.raises(ValueError, match="Placeholder hashes are strictly prohibited"):
        EpochManifest(
            code_identity="placeholder" + "0" * 29,
            working_tree_dirty=False,
            semantic_config_identity="0" * 64,
            is_test=True,
        )


    # 2. Malformed Git SHA rejection in manifest (not 40 hex chars)
    with pytest.raises(ValueError, match="must be 40-character hex"):
        EpochManifest(
            code_identity="tooshort",
            working_tree_dirty=False,
            semantic_config_identity="0" * 64,
            is_test=True,
        )

    # 3. Malformed semantic config hash rejection (not 64 hex chars)
    with pytest.raises(ValueError, match="must be 64-character SHA-256"):
        EpochManifest(
            code_identity="a" * 40,
            working_tree_dirty=False,
            semantic_config_identity="badlen",
            is_test=True,
        )

    # 4. Conflicting payload replay with same idempotency key
    store = ImmutableEventStore()
    ikey = compute_idempotency_key(EventType.DECISION_RECORD, "FIX-ADVERSARIAL", "2026-09-21T10:00:00Z")
    evt1 = ProspectiveEvent(
        event_type=EventType.DECISION_RECORD,
        epoch_id="EPOCH-001-TEST",
        idempotency_key=ikey,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
        payload={"ev": 0.05, "stake": 0.025},
    )
    store.append(evt1)

    evt2_tampered = ProspectiveEvent(
        event_type=EventType.DECISION_RECORD,
        epoch_id="EPOCH-001-TEST",
        idempotency_key=ikey,
        test_flag=TestFlag.TEST_ONLY_NON_PROSPECTIVE,
        payload={"ev": 0.12, "stake": 0.08},  # Tampered payload on same key
    )
    with pytest.raises(DuplicateIdempotencyCollisionError, match="Conflicting payload"):
        store.append(evt2_tampered)
