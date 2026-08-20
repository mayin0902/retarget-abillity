from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from pydantic import ValidationError

import retarget_agent.costing as costing
from retarget_agent.costing import (
    BudgetExceededError,
    BudgetLedger,
    CostEntry,
    CostType,
    GpuProbeStatus,
    IdempotencyConflictError,
    InvalidBudgetTransitionError,
    ResourceObservation,
    estimate_cpu_cost,
    estimate_gpu_cost,
    summarize_costs,
)


def entry(
    entry_id: str,
    *,
    estimated: str | None = "1.00",
    actual: str | None = None,
    currency: str = "CNY",
) -> CostEntry:
    return CostEntry(
        entry_id=entry_id,
        task_id="poster-01__square",
        call_id=f"call-{entry_id}",
        cost_type=CostType.DIRECT,
        estimated_amount=estimated,
        actual_amount=actual,
        currency=currency,
        pricing_source="provider price page 2026-08-11",
        quantity=1,
        unit="image",
    )


def test_cost_entry_requires_attribution_and_keeps_unknown_actual_null() -> None:
    cost = entry("seedream-01")
    assert cost.actual_amount is None
    assert cost.currency == "CNY"
    with pytest.raises(ValidationError, match="requires task, candidate, call or workflow"):
        CostEntry(
            entry_id="unowned",
            cost_type="operations",
            estimated_amount="1",
            currency="CNY",
            pricing_source="operator estimate",
            quantity=1,
            unit="hour",
        )
    with pytest.raises(ValidationError, match="must be supplied"):
        entry("unknown").model_copy(
            update={"estimated_amount": None, "actual_amount": None}
        ).model_validate(
            {**entry("unknown").model_dump(), "estimated_amount": None, "actual_amount": None}
        )


def test_reserve_commit_and_release_are_idempotent() -> None:
    ledger = BudgetLedger("10.00", "cny")
    first = ledger.reserve("call-1", "3.00")
    assert ledger.reserve("call-1", "3.00") == first
    committed = ledger.commit("call-1", "2.50")
    assert ledger.commit("call-1", "2.50") == committed

    second = ledger.reserve("call-2", "4.00")
    assert ledger.release("call-2").state == "released"
    assert ledger.release("call-2").state == "released"
    assert second.reserved_amount == Decimal("4.00")

    snapshot = ledger.snapshot()
    assert snapshot.reserved_amount == 0
    assert snapshot.committed_accounted_amount == Decimal("2.50")
    assert snapshot.actual_amount == Decimal("2.50")
    assert snapshot.remaining_amount == Decimal("7.50")


def test_unknown_actual_remains_null_and_holds_estimate_against_budget() -> None:
    ledger = BudgetLedger("5")
    ledger.reserve("call-1", "3")
    committed = ledger.commit("call-1")
    assert committed.actual_amount is None
    assert committed.accounted_amount == 3
    snapshot = ledger.snapshot()
    assert snapshot.actual_amount is None
    assert snapshot.known_actual_amount == 0
    assert snapshot.unknown_actual_count == 1
    assert snapshot.remaining_amount == 2


def test_hard_budget_and_transition_failures_do_not_mutate_ledger() -> None:
    ledger = BudgetLedger("5")
    ledger.reserve("call-1", "4")
    with pytest.raises(BudgetExceededError):
        ledger.reserve("call-2", "2")
    with pytest.raises(BudgetExceededError):
        ledger.commit("call-1", "6")
    assert ledger.get("call-1").state == "reserved"  # type: ignore[union-attr]
    with pytest.raises(IdempotencyConflictError):
        ledger.reserve("call-1", "3")
    ledger.release("call-1")
    with pytest.raises(InvalidBudgetTransitionError):
        ledger.commit("call-1")


def test_concurrent_duplicate_reservations_only_charge_once() -> None:
    ledger = BudgetLedger("1")
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda _: ledger.reserve("same-call", "0.75"), range(32)))
    assert len(records) == 32
    assert ledger.snapshot().reserved_amount == Decimal("0.75")


def test_compute_costs_include_utilization_and_optional_energy() -> None:
    gpu = estimate_gpu_cost(
        gpu_seconds=1800,
        card_hour_rate="4.00",
        utilization="0.50",
        energy_kwh="0.25",
        energy_rate_per_kwh="0.80",
    )
    assert gpu.compute_amount == Decimal("1.000")
    assert gpu.energy_amount == Decimal("0.2000")
    assert gpu.total_amount == Decimal("1.2000")

    cpu = estimate_cpu_cost(3600, "0.50", "0.25")
    assert cpu.compute_amount == Decimal("0.1250")
    assert cpu.total_amount == Decimal("0.1250")


def test_unpriced_energy_does_not_silently_become_zero() -> None:
    estimate = estimate_gpu_cost(60, "3", energy_kwh="0.01")
    assert estimate.energy_kwh == Decimal("0.01")
    assert estimate.energy_amount is None
    assert estimate.total_amount is None
    with pytest.raises(ValueError, match="energy_kwh is required"):
        estimate_gpu_cost(60, "3", energy_rate_per_kwh="0.5")


def test_cost_summary_uses_estimate_until_all_actuals_are_known() -> None:
    costs = [
        entry("one", estimated="0.6", actual="0.5"),
        entry("two", estimated="0.4", actual=None),
    ]
    summary = summarize_costs(costs, proxy_a_count=1, success_count=2)
    assert summary.actual_total is None
    assert summary.known_actual_total == Decimal("0.5")
    assert summary.unknown_actual_count == 1
    assert summary.cost_basis == "estimated"
    assert summary.cost_per_proxy_a == Decimal("1.0")
    assert summary.cost_per_success == Decimal("0.5")

    actual = summarize_costs(
        [entry("one", actual="0.5"), entry("two", actual="0.3")],
        proxy_a_count=2,
        success_count=2,
    )
    assert actual.cost_basis == "actual"
    assert actual.actual_total == Decimal("0.8")
    assert actual.cost_per_proxy_a == Decimal("0.4")


def test_cost_summary_rejects_mixed_currencies_and_zero_denominator_is_null() -> None:
    with pytest.raises(ValueError, match="summary currency"):
        summarize_costs(
            [entry("one"), entry("two", currency="USD")],
            proxy_a_count=1,
            success_count=1,
        )
    summary = summarize_costs([entry("one")], proxy_a_count=0, success_count=0)
    assert summary.cost_per_proxy_a is None
    assert summary.cost_per_success is None


def test_resource_observation_validates_gpu_utilization() -> None:
    observation = ResourceObservation(
        observation_id="candidate-01",
        candidate_id="candidate-01",
        wall_seconds=2.0,
        gpu_seconds=None,
        gpu_utilization=None,
    )
    assert observation.gpu_seconds is None
    with pytest.raises(ValidationError):
        observation.model_copy(update={"gpu_utilization": 1.1}).model_validate(
            {**observation.model_dump(), "gpu_utilization": 1.1}
        )


def test_environment_probe_is_safe_when_nvidia_smi_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(costing.shutil, "which", lambda _: None)
    snapshot = costing.capture_environment_snapshot()
    assert snapshot.gpu_probe_status is GpuProbeStatus.UNAVAILABLE
    assert snapshot.gpu_devices == ()
    assert snapshot.gpu_probe_error is None


def test_environment_probe_parses_nvidia_smi_without_torch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(costing.shutil, "which", lambda _: "nvidia-smi")

    def completed(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="0, NVIDIA L4, 23034, 555.42\n", stderr=""
        )

    monkeypatch.setattr(costing.subprocess, "run", completed)
    snapshot = costing.capture_environment_snapshot()
    assert snapshot.gpu_probe_status is GpuProbeStatus.AVAILABLE
    assert snapshot.gpu_devices[0].name == "NVIDIA L4"
    assert snapshot.gpu_devices[0].memory_total_mib == 23034


def test_environment_probe_reports_command_failure_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(costing.shutil, "which", lambda _: "nvidia-smi")

    def failed(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no driver")

    monkeypatch.setattr(costing.subprocess, "run", failed)
    snapshot = costing.capture_environment_snapshot()
    assert snapshot.gpu_probe_status is GpuProbeStatus.ERROR
    assert snapshot.gpu_probe_error == "no driver"
