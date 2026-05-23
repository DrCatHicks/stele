"""Admin GDPR console — the read side of erasure (M3.4, design doc §3.8/§3.10).

The withdrawal trigger and its tombstone semantics are covered in test_withdrawal;
here we cover GET /admin/withdrawals, the audit the admin UI lists. A triggered
withdrawal must show up in the audit, newest first, with its reason.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

VALID_DEFINITION: dict[str, Any] = {
    "pages": [
        {"name": "p1", "elements": [{"type": "radiogroup", "name": "q1", "choices": ["a", "b"]}]}
    ]
}
R1 = "11111111-1111-1111-1111-111111111111"
R2 = "22222222-2222-2222-2222-222222222222"


async def test_withdrawals_audit_lists_triggered_erasures(authed_client: AsyncClient) -> None:
    """A withdrawal trigger lands in the audit; the list returns it with reason,
    newest first."""
    empty = await authed_client.get("/admin/withdrawals")
    assert empty.status_code == 200
    before = {row["respondent_id"] for row in empty.json()}

    await authed_client.post(f"/respondents/{R1}/withdrawal", json={"reason": "ticket-1"})
    await authed_client.post(f"/respondents/{R2}/withdrawal", json={"reason": "ticket-2"})

    listing = await authed_client.get("/admin/withdrawals")
    assert listing.status_code == 200
    rows = listing.json()

    new_rows = [r for r in rows if r["respondent_id"] not in before]
    assert {r["respondent_id"] for r in new_rows} == {R1, R2}
    by_id = {r["respondent_id"]: r for r in new_rows}
    assert by_id[R1]["reason"] == "ticket-1"
    assert by_id[R2]["reason"] == "ticket-2"

    # Newest first: R2 was withdrawn after R1, so it sorts ahead among the new rows.
    new_in_order = [r["respondent_id"] for r in rows if r["respondent_id"] in {R1, R2}]
    assert new_in_order == [R2, R1]
