import asyncio

import pytest

from app.config import get_settings
from app.models import JobOrder, JobOrderStatus, PayModel
from app.services.zoho_export import export_job_orders_csv


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
    get_settings.cache_clear()


def test_zoho_job_orders_csv_header_golden():
    async def run():
        jobs = [
            JobOrder(
                id=1,
                client_org_id=1,
                created_by_user_id=1,
                title="Acme",
                job_type="Warehouse Worker",
                location_address="SG",
                location_lat=1.0,
                location_lng=103.0,
                workers_required_total=2,
                pay_model=PayModel.hourly,
                hourly_rate=10.0,
                per_shift_rate=None,
                status=JobOrderStatus.open,
            )
        ]
        csv_text = await export_job_orders_csv(None, jobs)
        first = csv_text.splitlines()[0]
        assert first == (
            "Deal_Name,Job_Type,Client_Org_Id,Location,Lat,Lng,Status,Workers_Required_Total,Pay_Model,Hourly_Rate,Per_Shift_Rate"
        )

    asyncio.run(run())


def test_timesheet_status_progression():
    """Document expected endorsement / verification pipeline (integration with DB is E2E)."""
    from app.models import TimesheetStatus

    order = [
        TimesheetStatus.pending,
        TimesheetStatus.employer_endorsed,
        TimesheetStatus.ta_verified,
        TimesheetStatus.payment_released,
    ]
    assert [s.value for s in order] == ["pending", "employer_endorsed", "ta_verified", "payment_released"]
    assert TimesheetStatus.disputed.value == "disputed"
    assert TimesheetStatus.client_payroll_released.value == "client_payroll_released"
