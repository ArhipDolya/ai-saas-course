import re
from datetime import date as CalendarDate
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import TransactionType
from app.transaction_rules import (
    MAX_AMOUNT,
    MAX_CATEGORY_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MIN_TRANSACTION_DATE,
    today,
)


class TransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: TransactionType
    amount: Decimal = Field(
        gt=0, le=MAX_AMOUNT, max_digits=12, decimal_places=2, allow_inf_nan=False
    )
    category: str = Field(min_length=1, max_length=MAX_CATEGORY_LENGTH)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    date: CalendarDate | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def normalize_amount(cls, value):
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
            if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value, flags=re.ASCII):
                raise ValueError("Вкажи суму з не більш ніж двома знаками після коми.")
        return value

    @field_validator("category", "description", mode="before")
    @classmethod
    def normalize_text(cls, value, info):
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("Текст містить недопустимий символ.")
            if info.field_name == "category":
                return " ".join(value.split())
            return value.strip() or None
        return value

    @field_validator("date", mode="before")
    @classmethod
    def validate_date_format(cls, value):
        if value is None or value == "":
            return None
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Дата має бути у форматі YYYY-MM-DD.")
        return value

    @field_validator("date")
    @classmethod
    def validate_date_range(cls, value):
        if value is not None and not MIN_TRANSACTION_DATE <= value <= today():
            raise ValueError("Дата має бути від 01.01.2000 до сьогодні (Europe/Kyiv).")
        return value
