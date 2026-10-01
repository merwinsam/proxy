from dataclasses import dataclass

EXPENSE_STATUSES = ["PENDING_REVIEW", "ALLOCATED", "DISALLOWED"]
LEDGER_TYPES = ["revenue", "expense", "tier_completed", "distribution", "correction"]


@dataclass
class Expense:
    id: int
    project_id: int
    vendor: str
    amount_cents: int
    description: str
    card_holder: str
    card_last4: str
    status: str
    created_on: str

    @classmethod
    def from_row(cls, row):
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            vendor=row["vendor"],
            amount_cents=row["amount_cents"],
            description=row["description"],
            card_holder=row["card_holder"],
            card_last4=row["card_last4"],
            status=row["status"],
            created_on=row["created_on"],
        )


@dataclass
class LedgerEntry:
    id: int
    project_id: int
    entry_date: str
    entry_type: str
    description: str
    amount_cents: int
    related_id: int

    @classmethod
    def from_row(cls, row):
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            entry_date=row["entry_date"],
            entry_type=row["entry_type"],
            description=row["description"],
            amount_cents=row["amount_cents"],
            related_id=row["related_id"],
        )
