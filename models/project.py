from dataclasses import dataclass


@dataclass
class Project:
    id: int
    proxy_id: str
    title: str
    project_type: str
    producer: str
    budget_cents: int
    revenue_received_cents: int
    distributable_cash_cents: int
    created_on: str

    @classmethod
    def from_row(cls, row):
        return cls(
            id=row["id"],
            proxy_id=row["proxy_id"],
            title=row["title"],
            project_type=row["project_type"],
            producer=row["producer"],
            budget_cents=row["budget_cents"],
            revenue_received_cents=row["revenue_received_cents"],
            distributable_cash_cents=row["distributable_cash_cents"],
            created_on=row["created_on"],
        )
