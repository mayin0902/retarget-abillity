"""Auditable cost accounting and lightweight resource observation contracts.

The module deliberately keeps provider pricing outside the core.  Callers supply
the rate, source and quantity that were in force for a run, while this module
preserves unknown actual charges as ``None`` and enforces a local hard budget.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _money(value: Decimal | int | str) -> Decimal:
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise ValueError("amount must be a finite non-negative decimal")
    return amount


def _currency(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
        raise ValueError("currency must be a three-letter ASCII code")
    return normalized


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CostType(StrEnum):
    DIRECT = "direct"
    INFRASTRUCTURE = "infrastructure"
    OPERATIONS = "operations"


class CostEntry(FrozenModel):
    """One priced quantity attributed to one or more workflow entities."""

    schema_version: str = "1.0"
    entry_id: str = Field(min_length=1)
    task_id: str | None = None
    candidate_id: str | None = None
    call_id: str | None = None
    workflow_id: str | None = None
    cost_type: CostType
    estimated_amount: Decimal | None = Field(default=None, ge=0)
    actual_amount: Decimal | None = Field(default=None, ge=0)
    currency: str
    pricing_source: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    unit: str = Field(min_length=1)

    _valid_currency = field_validator("currency")(_currency)

    @field_validator("estimated_amount", "actual_amount", "quantity")
    @classmethod
    def finite_decimal(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("cost values must be finite decimals")
        return value

    @model_validator(mode="after")
    def require_owner_and_cost_evidence(self) -> CostEntry:
        if not any((self.task_id, self.candidate_id, self.call_id, self.workflow_id)):
            raise ValueError("a cost entry requires task, candidate, call or workflow attribution")
        if self.estimated_amount is None and self.actual_amount is None:
            raise ValueError("estimated_amount or actual_amount must be supplied")
        return self


class ReservationState(StrEnum):
    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"


class BudgetExceededError(RuntimeError):
    """Raised when a reservation or a known actual charge exceeds the hard limit."""


class IdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused with different financial data."""


class InvalidBudgetTransitionError(RuntimeError):
    """Raised when a reservation cannot move to the requested state."""


class BudgetReservation(FrozenModel):
    idempotency_key: str = Field(min_length=1)
    reserved_amount: Decimal = Field(ge=0)
    actual_amount: Decimal | None = Field(default=None, ge=0)
    state: ReservationState

    @property
    def accounted_amount(self) -> Decimal:
        """Amount charged against the hard limit, conservatively using the estimate."""

        if self.state is ReservationState.RELEASED:
            return Decimal(0)
        if self.state is ReservationState.COMMITTED and self.actual_amount is not None:
            return self.actual_amount
        return self.reserved_amount


class BudgetSnapshot(FrozenModel):
    hard_limit: Decimal = Field(ge=0)
    currency: str
    reserved_amount: Decimal = Field(ge=0)
    committed_accounted_amount: Decimal = Field(ge=0)
    known_actual_amount: Decimal = Field(ge=0)
    actual_amount: Decimal | None = Field(default=None, ge=0)
    unknown_actual_count: int = Field(ge=0)
    remaining_amount: Decimal = Field(ge=0)

    _valid_currency = field_validator("currency")(_currency)


class BudgetLedger:
    """Thread-safe in-memory hard-budget ledger with idempotent operations.

    A committed reservation whose provider charge is not known keeps its estimate
    against the hard limit.  It never converts the unknown actual charge to zero.
    """

    def __init__(self, hard_limit: Decimal | int | str, currency: str = "CNY") -> None:
        self.hard_limit = _money(hard_limit)
        self.currency = _currency(currency)
        self._records: dict[str, BudgetReservation] = {}
        self._lock = threading.RLock()

    def reserve(
        self, idempotency_key: str, estimated_amount: Decimal | int | str
    ) -> BudgetReservation:
        key = self._key(idempotency_key)
        amount = _money(estimated_amount)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing.reserved_amount != amount:
                    raise IdempotencyConflictError(
                        f"idempotency key {key!r} was already used with a different amount"
                    )
                return existing
            if self._accounted_total_unlocked() + amount > self.hard_limit:
                raise BudgetExceededError("reservation would exceed the hard budget")
            record = BudgetReservation(
                idempotency_key=key,
                reserved_amount=amount,
                state=ReservationState.RESERVED,
            )
            self._records[key] = record
            return record

    def commit(
        self,
        idempotency_key: str,
        actual_amount: Decimal | int | str | None = None,
    ) -> BudgetReservation:
        key = self._key(idempotency_key)
        actual = None if actual_amount is None else _money(actual_amount)
        with self._lock:
            current = self._require_record_unlocked(key)
            if current.state is ReservationState.COMMITTED:
                if current.actual_amount != actual:
                    raise IdempotencyConflictError(
                        f"idempotency key {key!r} was already committed differently"
                    )
                return current
            if current.state is ReservationState.RELEASED:
                raise InvalidBudgetTransitionError("a released reservation cannot be committed")

            accounted = current.reserved_amount if actual is None else actual
            total_without_current = self._accounted_total_unlocked() - current.accounted_amount
            if total_without_current + accounted > self.hard_limit:
                raise BudgetExceededError("actual charge would exceed the hard budget")
            committed = current.model_copy(
                update={"actual_amount": actual, "state": ReservationState.COMMITTED}
            )
            self._records[key] = committed
            return committed

    def release(self, idempotency_key: str) -> BudgetReservation:
        key = self._key(idempotency_key)
        with self._lock:
            current = self._require_record_unlocked(key)
            if current.state is ReservationState.RELEASED:
                return current
            if current.state is ReservationState.COMMITTED:
                raise InvalidBudgetTransitionError("a committed charge cannot be released")
            released = current.model_copy(update={"state": ReservationState.RELEASED})
            self._records[key] = released
            return released

    def get(self, idempotency_key: str) -> BudgetReservation | None:
        key = self._key(idempotency_key)
        with self._lock:
            return self._records.get(key)

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            active = tuple(self._records.values())
            reserved = sum(
                (
                    record.reserved_amount
                    for record in active
                    if record.state is ReservationState.RESERVED
                ),
                Decimal(0),
            )
            committed = tuple(
                record for record in active if record.state is ReservationState.COMMITTED
            )
            committed_accounted = sum((record.accounted_amount for record in committed), Decimal(0))
            known_actual = sum(
                (record.actual_amount for record in committed if record.actual_amount is not None),
                Decimal(0),
            )
            unknown_count = sum(record.actual_amount is None for record in committed)
            return BudgetSnapshot(
                hard_limit=self.hard_limit,
                currency=self.currency,
                reserved_amount=reserved,
                committed_accounted_amount=committed_accounted,
                known_actual_amount=known_actual,
                actual_amount=None if unknown_count else known_actual,
                unknown_actual_count=unknown_count,
                remaining_amount=self.hard_limit - reserved - committed_accounted,
            )

    def _accounted_total_unlocked(self) -> Decimal:
        return sum((record.accounted_amount for record in self._records.values()), Decimal(0))

    def _require_record_unlocked(self, key: str) -> BudgetReservation:
        try:
            return self._records[key]
        except KeyError as error:
            raise KeyError(f"unknown reservation {key!r}") from error

    @staticmethod
    def _key(value: str) -> str:
        key = value.strip()
        if not key:
            raise ValueError("idempotency_key must not be empty")
        return key


class ComputeCostEstimate(FrozenModel):
    resource_type: Literal["gpu", "cpu"]
    duration_seconds: Decimal = Field(ge=0)
    hourly_rate: Decimal = Field(ge=0)
    utilization: Decimal = Field(ge=0, le=1)
    compute_amount: Decimal = Field(ge=0)
    energy_kwh: Decimal | None = Field(default=None, ge=0)
    energy_rate_per_kwh: Decimal | None = Field(default=None, ge=0)
    energy_amount: Decimal | None = Field(default=None, ge=0)
    total_amount: Decimal | None = Field(default=None, ge=0)
    currency: str

    _valid_currency = field_validator("currency")(_currency)


def estimate_gpu_cost(
    gpu_seconds: Decimal | int | str,
    card_hour_rate: Decimal | int | str,
    utilization: Decimal | int | str = 1,
    *,
    energy_kwh: Decimal | int | str | None = None,
    energy_rate_per_kwh: Decimal | int | str | None = None,
    currency: str = "CNY",
) -> ComputeCostEstimate:
    """Attribute card rental and optional energy cost to measured GPU utilization."""

    return _estimate_compute_cost(
        "gpu",
        gpu_seconds,
        card_hour_rate,
        utilization,
        energy_kwh,
        energy_rate_per_kwh,
        currency,
    )


def estimate_cpu_cost(
    cpu_seconds: Decimal | int | str,
    core_hour_rate: Decimal | int | str,
    utilization: Decimal | int | str = 1,
    *,
    energy_kwh: Decimal | int | str | None = None,
    energy_rate_per_kwh: Decimal | int | str | None = None,
    currency: str = "CNY",
) -> ComputeCostEstimate:
    """Attribute core rental and optional energy cost to measured CPU utilization."""

    return _estimate_compute_cost(
        "cpu",
        cpu_seconds,
        core_hour_rate,
        utilization,
        energy_kwh,
        energy_rate_per_kwh,
        currency,
    )


def _estimate_compute_cost(
    resource_type: Literal["gpu", "cpu"],
    duration_seconds: Decimal | int | str,
    hourly_rate: Decimal | int | str,
    utilization: Decimal | int | str,
    energy_kwh: Decimal | int | str | None,
    energy_rate_per_kwh: Decimal | int | str | None,
    currency: str,
) -> ComputeCostEstimate:
    seconds = _money(duration_seconds)
    rate = _money(hourly_rate)
    share = _money(utilization)
    if share > 1:
        raise ValueError("utilization must be between zero and one")
    energy = None if energy_kwh is None else _money(energy_kwh)
    energy_rate = None if energy_rate_per_kwh is None else _money(energy_rate_per_kwh)
    if energy is None and energy_rate is not None:
        raise ValueError("energy_kwh is required when an energy rate is supplied")

    compute_amount = seconds / Decimal(3600) * rate * share
    energy_amount = None if energy is None or energy_rate is None else energy * energy_rate
    total_amount = None if energy is not None and energy_rate is None else compute_amount
    if energy_amount is not None:
        total_amount = compute_amount + energy_amount
    return ComputeCostEstimate(
        resource_type=resource_type,
        duration_seconds=seconds,
        hourly_rate=rate,
        utilization=share,
        compute_amount=compute_amount,
        energy_kwh=energy,
        energy_rate_per_kwh=energy_rate,
        energy_amount=energy_amount,
        total_amount=total_amount,
        currency=currency,
    )


class CostSummary(FrozenModel):
    currency: str
    entry_count: int = Field(ge=0)
    estimated_total: Decimal | None = Field(default=None, ge=0)
    actual_total: Decimal | None = Field(default=None, ge=0)
    known_actual_total: Decimal = Field(ge=0)
    unknown_actual_count: int = Field(ge=0)
    cost_basis: Literal["actual", "estimated", "unknown"]
    basis_total: Decimal | None = Field(default=None, ge=0)
    proxy_a_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    cost_per_proxy_a: Decimal | None = Field(default=None, ge=0)
    cost_per_success: Decimal | None = Field(default=None, ge=0)

    _valid_currency = field_validator("currency")(_currency)


def summarize_costs(
    entries: list[CostEntry] | tuple[CostEntry, ...],
    *,
    proxy_a_count: int,
    success_count: int,
    currency: str | None = None,
) -> CostSummary:
    """Summarize costs and normalize them by proxy-A and technical successes.

    Actual totals are only reported when every entry has an actual charge.  Until
    then the summary selects a complete estimated total, if available, as its
    normalization basis.
    """

    if proxy_a_count < 0 or success_count < 0:
        raise ValueError("counts must be non-negative")
    normalized_currency = _currency(currency or (entries[0].currency if entries else "CNY"))
    if any(entry.currency != normalized_currency for entry in entries):
        raise ValueError("all cost entries must use the summary currency")

    estimated_complete = bool(entries) and all(
        entry.estimated_amount is not None for entry in entries
    )
    actual_complete = bool(entries) and all(entry.actual_amount is not None for entry in entries)
    estimated_total = (
        sum(
            (entry.estimated_amount for entry in entries if entry.estimated_amount is not None),
            Decimal(0),
        )
        if estimated_complete
        else None
    )
    known_actual_total = sum(
        (entry.actual_amount for entry in entries if entry.actual_amount is not None), Decimal(0)
    )
    actual_total = known_actual_total if actual_complete else None
    if actual_total is not None:
        basis: Literal["actual", "estimated", "unknown"] = "actual"
        basis_total = actual_total
    elif estimated_total is not None:
        basis = "estimated"
        basis_total = estimated_total
    else:
        basis = "unknown"
        basis_total = None
    return CostSummary(
        currency=normalized_currency,
        entry_count=len(entries),
        estimated_total=estimated_total,
        actual_total=actual_total,
        known_actual_total=known_actual_total,
        unknown_actual_count=sum(entry.actual_amount is None for entry in entries),
        cost_basis=basis,
        basis_total=basis_total,
        proxy_a_count=proxy_a_count,
        success_count=success_count,
        cost_per_proxy_a=None
        if basis_total is None or proxy_a_count == 0
        else basis_total / proxy_a_count,
        cost_per_success=None
        if basis_total is None or success_count == 0
        else basis_total / success_count,
    )


class GpuDeviceSnapshot(FrozenModel):
    index: int = Field(ge=0)
    name: str
    memory_total_mib: int | None = Field(default=None, ge=0)
    driver_version: str | None = None


class GpuProbeStatus(StrEnum):
    AVAILABLE = "available"
    NO_GPU = "no_gpu"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class EnvironmentSnapshot(FrozenModel):
    schema_version: str = "1.0"
    captured_at: datetime = Field(default_factory=_utc_now)
    python_version: str
    platform: str
    machine: str
    processor: str
    cpu_logical_count: int | None = Field(default=None, ge=1)
    memory_total_bytes: int | None = Field(default=None, ge=0)
    gpu_probe_status: GpuProbeStatus
    gpu_devices: tuple[GpuDeviceSnapshot, ...] = ()
    gpu_probe_error: str | None = None


class ResourceObservation(FrozenModel):
    schema_version: str = "1.0"
    observation_id: str = Field(min_length=1)
    task_id: str | None = None
    candidate_id: str | None = None
    call_id: str | None = None
    workflow_id: str | None = None
    observed_at: datetime = Field(default_factory=_utc_now)
    wall_seconds: float | None = Field(default=None, ge=0)
    cpu_seconds: float | None = Field(default=None, ge=0)
    peak_rss_bytes: int | None = Field(default=None, ge=0)
    gpu_seconds: float | None = Field(default=None, ge=0)
    gpu_utilization: float | None = Field(default=None, ge=0, le=1)
    gpu_memory_peak_bytes: int | None = Field(default=None, ge=0)
    energy_kwh: float | None = Field(default=None, ge=0)


def capture_environment_snapshot(timeout_seconds: float = 2.0) -> EnvironmentSnapshot:
    """Capture a small environment record without importing a GPU framework."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    gpu_status, devices, error = _probe_nvidia_smi(timeout_seconds)
    return EnvironmentSnapshot(
        python_version=platform.python_version(),
        platform=platform.platform(),
        machine=platform.machine(),
        processor=platform.processor(),
        cpu_logical_count=os.cpu_count(),
        memory_total_bytes=_memory_total_bytes(),
        gpu_probe_status=gpu_status,
        gpu_devices=devices,
        gpu_probe_error=error,
    )


def _probe_nvidia_smi(
    timeout_seconds: float,
) -> tuple[GpuProbeStatus, tuple[GpuDeviceSnapshot, ...], str | None]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return GpuProbeStatus.UNAVAILABLE, (), None
    command = [
        executable,
        "--query-gpu=index,name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return GpuProbeStatus.ERROR, (), str(error)[:300]
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "nvidia-smi failed").strip()
        return GpuProbeStatus.ERROR, (), message[:300]
    devices: list[GpuDeviceSnapshot] = []
    try:
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            index, name, memory_mib, driver = (part.strip() for part in line.split(",", 3))
            devices.append(
                GpuDeviceSnapshot(
                    index=int(index),
                    name=name,
                    memory_total_mib=int(memory_mib),
                    driver_version=driver,
                )
            )
    except (TypeError, ValueError) as error:
        return GpuProbeStatus.ERROR, (), f"invalid nvidia-smi output: {error}"[:300]
    if not devices:
        return GpuProbeStatus.NO_GPU, (), None
    return GpuProbeStatus.AVAILABLE, tuple(devices), None


def _memory_total_bytes() -> int | None:
    if hasattr(os, "sysconf"):
        try:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            page_count = int(os.sysconf("SC_PHYS_PAGES"))
            return page_size * page_count
        except (OSError, TypeError, ValueError):
            pass
    if platform.system() == "Windows":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_physical)
        except (AttributeError, OSError, TypeError, ValueError):
            pass
    return None
