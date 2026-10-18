from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    balance: int


class UserWithToken(UserOut):
    token: str


class DealCreate(BaseModel):
    item: str = Field(min_length=1, max_length=60)
    price: int = Field(ge=100, le=1_000_000)
    hours: Literal[24, 48, 72] = 24


class DealOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    item: str
    price: int
    hours: int
    status: str
    seller_id: int
    buyer_id: int | None
    expires_at: datetime | None
    created_at: datetime


class CodeIn(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class ResolveIn(BaseModel):
    to_buyer: bool


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: str
    user_id: int | None
    amount: int | None
    created_at: datetime
