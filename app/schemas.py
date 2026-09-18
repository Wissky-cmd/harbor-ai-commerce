from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(StrictModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=256)


class QuoteInput(StrictModel):
    product_id: str
    customer_id: str
    quantity: int = Field(ge=1, le=1000000)
    fx_rate: Decimal = Field(default=Decimal("7.20"), ge=Decimal("0.01"), le=1000, max_digits=10, decimal_places=4)
    margin: Decimal = Field(default=Decimal("0.30"), ge=Decimal("0.05"), le=Decimal("0.80"), max_digits=5, decimal_places=4)
    packaging_cny: Decimal = Field(default=Decimal("3"), ge=0, le=10000, max_digits=10, decimal_places=2)
    domestic_cny: Decimal = Field(default=Decimal("2"), ge=0, le=10000, max_digits=10, decimal_places=2)
    freight_usd: Decimal = Field(default=Decimal("1.5"), ge=0, le=10000, max_digits=10, decimal_places=2)
    duty_rate: Decimal = Field(default=Decimal("0.12"), ge=0, le=1, max_digits=5, decimal_places=4)
    incoterm: Literal["FOB", "DDP"] = "FOB"


class OrderTransition(StrictModel):
    stage: Literal["sampling", "production", "inspection", "shipping", "delivered", "closed"]
    progress: int = Field(ge=0, le=100)
    version: int = Field(ge=1)
    note: str = Field(default="", max_length=2000)
    quality_passed: bool = False


class Payment(StrictModel):
    amount: Decimal = Field(gt=0, le=100000000, max_digits=12, decimal_places=2)
    version: int = Field(ge=1)


class AgentInput(StrictModel):
    agent: Literal["buyer", "sales", "merchandiser", "quote", "support", "analyst", "retro"]
    question: str = Field(min_length=2, max_length=2000)


class KnowledgeInput(StrictModel):
    title: str = Field(min_length=2, max_length=150)
    category: str = Field(min_length=2, max_length=50)
    content: str = Field(min_length=20, max_length=30000)
    min_role: Literal["viewer", "sales", "operations", "admin"] = "viewer"


class SearchInput(StrictModel):
    question: str = Field(min_length=2, max_length=2000)


class CustomerInput(StrictModel):
    name: str = Field(min_length=2, max_length=150)
    country: str = Field(min_length=1, max_length=80)
    channel: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
