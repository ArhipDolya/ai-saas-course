from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

MAX_AMOUNT = Decimal("9999999999.99")
MAX_CATEGORY_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 255
MIN_TRANSACTION_DATE = date(2000, 1, 1)
TRANSACTION_TIMEZONE = ZoneInfo("Europe/Kyiv")


def today() -> date:
    """Business date shared by API and bot, independent of the container timezone."""
    return datetime.now(TRANSACTION_TIMEZONE).date()
