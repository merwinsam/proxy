from dataclasses import dataclass

ENTITLEMENT_TYPES = [
    "percentage",
    "fixed_recoupment",
    "recoupment_plus_premium",
    "continuing_participation",
]

ROLES = ["Writer", "Director", "Producer", "Investor", "Actor", "Composer", "Other"]


@dataclass
class Stakeholder:
    id: int
    project_id: int
    creator_id: str
    name: str
    role: str
    points: float
    entitlement_type: str
    investment_amount_cents: int
    premium_pct: float
    pool: str
    kyc_status: str

    @classmethod
    def from_row(cls, row):
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            creator_id=row["creator_id"],
            name=row["name"],
            role=row["role"],
            points=row["points"],
            entitlement_type=row["entitlement_type"],
            investment_amount_cents=row["investment_amount_cents"],
            premium_pct=row["premium_pct"],
            pool=row["pool"],
            kyc_status=row["kyc_status"],
        )
