from dataclasses import dataclass


@dataclass
class Order:
    id: int
    customer: str
    status: str          # created | paid | shipped | cancelled
    amount_cents: int    # 金额统一以「分」存储
