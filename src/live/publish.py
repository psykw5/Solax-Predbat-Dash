"""Secure publication of the privacy-first Wattson monthly summary."""

from __future__ import annotations

import calendar
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import unified_diff
from pathlib import Path
from typing import Any

from live.config import DEFAULT_LIVE_DIR, DEFAULT_PUBLIC_SNAPSHOT_PATH, load_environment
from live.store import LiveStore
from metrics import EnergyMetrics
from utils.redaction import text_hash

DEFAULT_WEBSITE_REPO_PATH = Path("..") / "kevinwatson.dev"
MONTHLY_DESTINATION_RELATIVE_PATH = Path("src/data/wattson-monthly-summary.json")
MONTHLY_PUBLIC_SNAPSHOT_PATH = Path("data/public/wattson-monthly-summary.json")
FINANCIAL_DIR = Path("data/processed/financial")
ENERGY_INTERVALS_PATH = Path("data/processed/solax/solax_intervals.parquet")
MAX_FRESHNESS_MINUTES = 24 * 60
LOCK_PATH = DEFAULT_LIVE_DIR / "public-dashboard.lock"
WEBSITE_CHECK_COMMANDS = (
    ("pnpm", "lint"),
    ("pnpm", "check"),
    ("pnpm", "format:check"),
)
PRIVATE_LIVE_SNAPSHOT_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "generated_at": str,
    "data_as_of": str,
    "current_pv_power_kw": (float, int, type(None)),
    "current_battery_percentage": (float, int, type(None)),
    "current_battery_direction": (str, type(None)),
    "current_battery_power_kw": (float, int, type(None)),
    "current_grid_direction": (str, type(None)),
    "current_grid_power_kw": (float, int, type(None)),
    "todays_generation_kwh": (float, int, type(None)),
    "current_import_rate_p_per_kwh": (float, int, type(None)),
    "current_export_rate_p_per_kwh": (float, int, type(None)),
    "next_tariff_change": (str, type(None)),
    "next_rate_p_per_kwh": (float, int, type(None)),
    "confirmed_lifetime_financial_benefit_gbp": (float, int, type(None)),
    "nominal_recovery_percentage": (float, int, type(None)),
    "discounted_recovery_percentage": (float, int, type(None)),
    "simple_payback_month": (str, type(None)),
    "discounted_payback_month": (str, type(None)),
    "health_status": str,
    "freshness_minutes": int,
}
MONTHLY_PUBLIC_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "reporting_month": str,
    "publication_month": str,
    "lifetime_generation_kwh": (float, int),
    "lifetime_self_consumed_energy_kwh": (float, int),
    "lifetime_export_kwh": (float, int),
    "lifetime_financial_benefit_gbp": (float, int),
    "monthly_generation_kwh": (float, int),
    "monthly_avoided_import_value_gbp": (float, int),
    "monthly_export_income_gbp": (float, int),
    "monthly_total_benefit_gbp": (float, int),
    "nominal_recovery_percentage": (float, int),
    "discounted_recovery_percentage": (float, int),
    "simple_payback_month": str,
    "discounted_payback_month": str,
    "annual_summaries": list,
    "data_quality_status": str,
}
FORBIDDEN_PUBLIC_MONTHLY_KEYS = {
    "generated_at",
    "data_as_of",
    "current_pv_power_kw",
    "current_battery_percentage",
    "current_battery_direction",
    "current_battery_power_kw",
    "current_grid_direction",
    "current_grid_power_kw",
    "todays_generation_kwh",
    "current_import_rate_p_per_kwh",
    "current_export_rate_p_per_kwh",
    "next_tariff_change",
    "next_rate_p_per_kwh",
    "freshness_minutes",
}
FORBIDDEN_TEXT = re.compile(
    r"(account|mpan|mprn|serial|wi-?fi|token|api[_ -]?key|secret|password|"
    r"address|postcode|coordinate|endpoint|filename|\\|/users/|onedrive)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PublicationResult:
    status: str
    source_path: Path
    destination_path: Path
    message: str
    source_snapshot_hash: str | None = None
    website_commit_hash: str | None = None
    material_changed_fields: list[str] | None = None


def build_monthly_public_snapshot(
    reporting_month: str | None = None,
    financial_dir: Path = FINANCIAL_DIR,
    energy_path: Path = ENERGY_INTERVALS_PATH,
    output_path: Path = MONTHLY_PUBLIC_SNAPSHOT_PATH,
    today: date | None = None,
) -> dict[str, Any]:
    run_date = today or datetime.now(UTC).date()
    month = reporting_month or previous_month(run_date)
    ensure_previous_month_complete(month, run_date)

    monthly = read_csv_dicts(financial_dir / "monthly_financial_summary.csv")
    annual = read_csv_dicts(financial_dir / "annual_financial_summary.csv")
    lifetime = load_json_any(financial_dir / "lifetime_summary.json")
    payback = load_json_any(financial_dir / "payback_public_summary.json")
    row = find_period(monthly, month)
    if row is None:
        raise ValueError(f"No monthly financial row found for reporting month {month}.")
    if int(float(row.get("included_intervals", 0))) < expected_half_hours(month):
        raise ValueError(f"Reporting month {month} has incomplete monthly financial data.")
    if lifetime.get("total_financial_benefit_status") != "exact_tariff_rate_reconstructed_energy":
        raise ValueError("Latest monthly financial calculation has not passed validation.")

    metrics = EnergyMetrics(energy_path)
    coverage_start, coverage_end = metrics.coverage_range()
    lifetime_generation = metrics.total_generation(coverage_start, coverage_end)
    lifetime_export = metrics.total_export(coverage_start, coverage_end)
    lifetime_self_consumption = metrics.self_consumption(coverage_start, coverage_end)
    report_year, report_month = parse_month(month)
    monthly_energy = metrics.monthly_summary(report_year, report_month)
    annual_energy = [
        metrics.annual_summary(year) for year in range(coverage_start.year, coverage_end.year + 1)
    ]

    snapshot = {
        "reporting_month": month,
        "publication_month": run_date.strftime("%Y-%m"),
        "lifetime_generation_kwh": round(lifetime_generation.kwh, 1),
        "lifetime_self_consumed_energy_kwh": round(lifetime_self_consumption.self_consumed_kwh, 1),
        "lifetime_export_kwh": round(lifetime_export.kwh, 1),
        "lifetime_financial_benefit_gbp": round(float(lifetime["confirmed_financial_benefit"]), 2),
        "monthly_generation_kwh": round(monthly_energy.generation_kwh, 1),
        "monthly_avoided_import_value_gbp": round(float(row["avoided_import_value"]), 2),
        "monthly_export_income_gbp": round(float(row["export_income"]), 2),
        "monthly_total_benefit_gbp": round(float(row["total_financial_benefit"]), 2),
        "nominal_recovery_percentage": round(float(payback["nominal_recovery_percentage"]), 2),
        "discounted_recovery_percentage": round(
            float(payback["discounted_recovery_percentage"]), 2
        ),
        "simple_payback_month": str(payback["projected_simple_payback_month"]),
        "discounted_payback_month": str(payback["projected_discounted_payback_month"]),
        "annual_summaries": annual_public_summaries(annual, annual_energy),
        "data_quality_status": "validated_monthly_financials",
    }
    validate_monthly_public_snapshot(snapshot)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return snapshot


def publish_monthly_summary(
    source_path: Path = MONTHLY_PUBLIC_SNAPSHOT_PATH,
    website_repo_path: Path | None = None,
    store: LiveStore | None = None,
    use_lock: bool = True,
) -> PublicationResult:
    target_repo = website_repo_path or configured_website_repo_path()
    context = update_lock() if use_lock else null_lock()
    with context:
        return publish_monthly_summary_locked(source_path, target_repo, store)


def publish_monthly_summary_locked(
    source_path: Path,
    target_repo: Path,
    store: LiveStore | None,
) -> PublicationResult:
    validate_website_repository(target_repo)
    destination = target_repo / MONTHLY_DESTINATION_RELATIVE_PATH
    source = load_monthly_public_snapshot(source_path)
    existing = load_monthly_public_snapshot(destination) if destination.exists() else None
    if existing and existing["reporting_month"] == source["reporting_month"]:
        return PublicationResult(
            status="unchanged",
            source_path=source_path,
            destination_path=destination,
            message="Reporting month has already been published.",
            source_snapshot_hash=snapshot_hash(source),
            material_changed_fields=[],
        )
    previous_content = destination.read_text(encoding="utf-8") if destination.exists() else None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        run_website_checks(target_repo)
        stage_only_path(target_repo, MONTHLY_DESTINATION_RELATIVE_PATH)
        commit_hash = commit_and_push_website(target_repo)
    except Exception:
        restore_destination(destination, previous_content)
        unstage_path(target_repo, MONTHLY_DESTINATION_RELATIVE_PATH)
        raise
    result = PublicationResult(
        status="published",
        source_path=source_path,
        destination_path=destination,
        message="Monthly website snapshot committed and pushed.",
        source_snapshot_hash=snapshot_hash(source),
        website_commit_hash=commit_hash,
        material_changed_fields=material_changed_fields(existing, source),
    )
    record_publication(store, datetime.now(UTC), result)
    return result


def publish_public_snapshot(
    source_path: Path = DEFAULT_PUBLIC_SNAPSHOT_PATH,
    website_repo_path: Path | None = None,
    now: datetime | None = None,
) -> PublicationResult:
    _ = website_repo_path
    _ = now
    source = load_public_snapshot(source_path)
    validate_private_live_snapshot(source)
    return PublicationResult(
        status="validated",
        source_path=source_path,
        destination_path=source_path,
        message="Private live snapshot validated locally; no website publication performed.",
        source_snapshot_hash=snapshot_hash(source),
        material_changed_fields=[],
    )


def publish_website_snapshot(
    source_path: Path = DEFAULT_PUBLIC_SNAPSHOT_PATH,
    website_repo_path: Path | None = None,
    now: datetime | None = None,
    store: LiveStore | None = None,
    use_lock: bool = True,
) -> PublicationResult:
    _ = source_path
    _ = website_repo_path
    _ = now
    _ = store
    _ = use_lock
    raise ValueError("Live snapshot website publication is disabled; use publish_monthly_summary.")


def publish_website_snapshot_locked(
    source_path: Path,
    target_repo: Path,
    generated_at: datetime,
    store: LiveStore | None,
) -> PublicationResult:
    _ = source_path
    _ = target_repo
    _ = generated_at
    _ = store
    raise ValueError("Live snapshot website publication is disabled; use publish_monthly_summary.")


def configured_website_repo_path() -> Path:
    configured = load_environment().get("WATTSON_WEBSITE_REPO_PATH")
    return Path(configured) if configured else DEFAULT_WEBSITE_REPO_PATH


def validate_website_repository(path: Path) -> None:
    resolved = path.resolve()
    if not resolved.exists():
        raise ValueError("Website repository path does not exist.")
    if not (resolved / ".git").exists():
        raise ValueError("Website repository path is not a Git repository.")
    if not (resolved / "astro.config.mjs").exists():
        raise ValueError("Website repository does not contain expected Astro config.")
    if not (resolved / "package.json").exists():
        raise ValueError("Website repository does not contain expected package.json.")
    if not (resolved / "src" / "data").exists():
        raise ValueError("Website repository does not contain expected src/data directory.")
    if git(resolved, "remote", "get-url", "origin").returncode != 0:
        raise ValueError("Website repository has no remote origin.")
    branch = git_text(resolved, "branch", "--show-current")
    if branch != "main":
        raise ValueError("Website repository must be on branch main.")
    if git_text(resolved, "status", "--short"):
        raise ValueError("Website repository working tree must be clean before publication.")


def load_public_snapshot(path: Path) -> dict[str, Any]:
    if path.name != "wattson-live-summary.json":
        raise ValueError("Only wattson-live-summary.json may be validated as a private snapshot.")
    return json.loads(path.read_text(encoding="utf-8"))


def load_monthly_public_snapshot(path: Path) -> dict[str, Any]:
    if path.name != "wattson-monthly-summary.json":
        raise ValueError("Only wattson-monthly-summary.json may be published monthly.")
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    validate_monthly_public_snapshot(snapshot)
    return snapshot


def validate_monthly_public_snapshot(snapshot: dict[str, Any]) -> None:
    keys = set(snapshot)
    expected = set(MONTHLY_PUBLIC_SCHEMA)
    if keys != expected:
        extra = sorted(keys - expected)
        missing = sorted(expected - keys)
        raise ValueError(f"Monthly snapshot schema mismatch. Extra={extra}; missing={missing}.")
    if keys & FORBIDDEN_PUBLIC_MONTHLY_KEYS:
        raise ValueError("Monthly snapshot contains live or operational fields.")
    for key, expected_type in MONTHLY_PUBLIC_SCHEMA.items():
        if not isinstance(snapshot[key], expected_type):
            raise ValueError(f"Monthly snapshot field has invalid type: {key}.")
    parse_month(snapshot["reporting_month"])
    parse_month(snapshot["publication_month"])
    if contains_private_text(snapshot):
        raise ValueError("Monthly snapshot failed privacy validation.")
    validate_annual_summaries(snapshot["annual_summaries"])


def validate_annual_summaries(rows: list[object]) -> None:
    required = {
        "year",
        "generation_kwh",
        "avoided_import_value_gbp",
        "export_income_gbp",
        "total_benefit_gbp",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("Annual summaries must use the strict public schema.")
        if not isinstance(row["year"], int):
            raise ValueError("Annual summary year must be an integer.")


def validate_private_live_snapshot(snapshot: dict[str, Any]) -> None:
    keys = set(snapshot)
    expected = set(PRIVATE_LIVE_SNAPSHOT_SCHEMA)
    if keys != expected:
        extra = sorted(keys - expected)
        missing = sorted(expected - keys)
        raise ValueError(
            f"Private live snapshot schema mismatch. Extra={extra}; missing={missing}."
        )
    for key, expected_type in PRIVATE_LIVE_SNAPSHOT_SCHEMA.items():
        if not isinstance(snapshot[key], expected_type):
            raise ValueError(f"Private live snapshot field has invalid type: {key}.")
    if snapshot["health_status"] != "ok":
        raise ValueError("Snapshot health_status must be ok.")
    freshness = snapshot["freshness_minutes"]
    if freshness < 30 or freshness > MAX_FRESHNESS_MINUTES:
        raise ValueError("Snapshot freshness is outside the allowed range.")
    if contains_private_text(snapshot):
        raise ValueError("Snapshot failed privacy validation.")
    for timestamp_key in ["generated_at", "data_as_of"]:
        parse_public_timestamp(snapshot[timestamp_key])
    if snapshot["next_tariff_change"] is not None:
        parse_public_timestamp(snapshot["next_tariff_change"])


def material_fingerprint(snapshot: dict[str, Any]) -> str:
    material = {
        key: value
        for key, value in snapshot.items()
        if key not in {"generated_at", "freshness_minutes"}
    }
    return json.dumps(material, sort_keys=True, separators=(",", ":"))


def material_changed_fields(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> list[str]:
    if existing is None:
        return sorted(key for key in incoming if key not in {"generated_at", "freshness_minutes"})
    ignored = {"generated_at", "freshness_minutes"}
    return sorted(
        key for key in incoming if key not in ignored and existing.get(key) != incoming.get(key)
    )


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    return text_hash(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))


def contains_private_text(value: Any) -> bool:
    if isinstance(value, dict):
        return any(contains_private_text(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_private_text(item) for item in value)
    if isinstance(value, str):
        return FORBIDDEN_TEXT.search(value) is not None
    return False


def parse_public_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def run_website_checks(path: Path) -> None:
    for command in WEBSITE_CHECK_COMMANDS:
        result = subprocess.run(command, cwd=path, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"Website validation command failed: {' '.join(command)}")


def stage_only_path(path: Path, relative_path: Path) -> None:
    git_required(path, "add", relative_path.as_posix())
    staged = git_text(path, "diff", "--cached", "--name-only").splitlines()
    if staged != [relative_path.as_posix()]:
        raise ValueError(f"Unexpected staged files: {staged}")


def commit_and_push_website(path: Path) -> str:
    git_required(path, "commit", "-m", "data: update Wattson public snapshot")
    commit_hash = git_text(path, "rev-parse", "HEAD")
    try:
        git_required(path, "push", "origin", "main")
    except Exception:
        git_required(path, "reset", "--soft", "HEAD~1")
        raise
    return commit_hash


def git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    safe_directory = f"safe.directory={path.resolve().as_posix()}"
    return subprocess.run(
        ["git", "-c", safe_directory, *args],
        cwd=path,
        check=False,
        capture_output=True,
        text=True,
    )


def git_required(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = git(path, *args)
    if result.returncode != 0:
        raise ValueError(f"Git command failed: {' '.join(args)}")
    return result


def git_text(path: Path, *args: str) -> str:
    return git_required(path, *args).stdout.strip()


def record_publication(
    store: LiveStore | None,
    published_at: datetime,
    result: PublicationResult,
) -> None:
    if store is None or result.source_snapshot_hash is None:
        return
    store.insert_publication_run(
        published_at=published_at,
        source_snapshot_hash=result.source_snapshot_hash,
        website_commit_hash=result.website_commit_hash,
        status=result.status,
        message=result.message,
    )


class update_lock:
    def __init__(self, path: Path = LOCK_PATH) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> update_lock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.fd, str(os.getpid()).encode("ascii"))
        except FileExistsError as exc:
            raise ValueError("Another public dashboard update is already running.") from exc
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)


class null_lock:
    def __enter__(self) -> null_lock:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


def restore_destination(destination: Path, previous_content: str | None) -> None:
    if previous_content is None:
        destination.unlink(missing_ok=True)
    else:
        destination.write_text(previous_content, encoding="utf-8")


def unstage_path(path: Path, relative_path: Path) -> None:
    result = git(path, "restore", "--staged", relative_path.as_posix())
    if result.returncode != 0:
        git(path, "reset", "--", relative_path.as_posix())


def proposed_diff(source_path: Path, website_repo_path: Path | None = None) -> str:
    target_repo = website_repo_path or configured_website_repo_path()
    destination = target_repo / MONTHLY_DESTINATION_RELATIVE_PATH
    source = load_monthly_public_snapshot(source_path)
    source_text = json.dumps(source, indent=2, sort_keys=True) + "\n"
    if destination.exists():
        load_monthly_public_snapshot(destination)
        existing_text = destination.read_text(encoding="utf-8")
    else:
        existing_text = ""
    return "".join(
        unified_diff(
            existing_text.splitlines(keepends=True),
            source_text.splitlines(keepends=True),
            fromfile=str(destination),
            tofile=str(source_path),
        )
    )


def previous_month(value: date) -> str:
    year = value.year
    month = value.month - 1
    if month == 0:
        year -= 1
        month = 12
    return f"{year:04d}-{month:02d}"


def ensure_previous_month_complete(reporting_month: str, today: date) -> None:
    report_year, report_month = parse_month(reporting_month)
    first_current = date(today.year, today.month, 1)
    first_after_report = add_month(date(report_year, report_month, 1))
    if first_after_report > first_current:
        raise ValueError("Reporting month is not complete.")


def parse_month(value: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("Month values must use YYYY-MM format.")
    year, month = [int(part) for part in value.split("-")]
    if month < 1 or month > 12:
        raise ValueError("Month value is out of range.")
    return year, month


def add_month(value: date) -> date:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    return date(year, month, 1)


def expected_half_hours(month_value: str) -> int:
    year, month = parse_month(month_value)
    return calendar.monthrange(year, month)[1] * 48


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_json_any(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_period(rows: list[dict[str, str]], period: str) -> dict[str, str] | None:
    return next((row for row in rows if row.get("period") == period), None)


def annual_public_summaries(
    rows: list[dict[str, str]],
    energy_rows: list[Any],
) -> list[dict[str, object]]:
    energy_by_year = {row.year: row for row in energy_rows}
    return [
        {
            "year": int(row["period"]),
            "generation_kwh": round(energy_by_year[int(row["period"])].generation_kwh, 1),
            "avoided_import_value_gbp": round(float(row["avoided_import_value"]), 2),
            "export_income_gbp": round(float(row["export_income"]), 2),
            "total_benefit_gbp": round(float(row["total_financial_benefit"]), 2),
        }
        for row in rows
    ]
