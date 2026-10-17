"""Shared enums, constrained types and validators used across feature models."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

IST = timezone(timedelta(hours=5, minutes=30))
EARLIEST_DATE = date(2000, 1, 1)


class Language(StrEnum):
    """Supported UI and AI response languages."""

    EN = "en"
    HI = "hi"
    PA = "pa"
    TA = "ta"
    BN = "bn"
    MR = "mr"


LANGUAGE_NAMES: dict[Language, str] = {
    Language.EN: "English",
    Language.HI: "Hindi (Devanagari script)",
    Language.PA: "Punjabi (Gurmukhi script)",
    Language.TA: "Tamil (Tamil script)",
    Language.BN: "Bengali (Bengali script)",
    Language.MR: "Marathi (Devanagari script)",
}


class Platform(StrEnum):
    """Work platforms a worker can pick. ``other`` covers contractors and any other employer."""

    SWIGGY = "swiggy"
    ZOMATO = "zomato"
    UBER = "uber"
    OLA = "ola"
    RAPIDO = "rapido"
    URBAN_COMPANY = "urban_company"
    BHARAT_TAXI = "bharat_taxi"
    PORTER = "porter"
    BLINKIT = "blinkit"
    ZEPTO = "zepto"
    OTHER = "other"


PLATFORM_NAMES: dict[Platform, str] = {
    Platform.SWIGGY: "Swiggy",
    Platform.ZOMATO: "Zomato",
    Platform.UBER: "Uber",
    Platform.OLA: "Ola",
    Platform.RAPIDO: "Rapido",
    Platform.URBAN_COMPANY: "Urban Company",
    Platform.BHARAT_TAXI: "Bharat Taxi",
    Platform.PORTER: "Porter",
    Platform.BLINKIT: "Blinkit",
    Platform.ZEPTO: "Zepto",
    Platform.OTHER: "Other / contractor",
}


class WorkType(StrEnum):
    """Kinds of work. A worker may do several."""

    DELIVERY = "delivery"
    RIDE_HAILING = "ride_hailing"
    BIKE_TAXI = "bike_taxi"
    HOME_SERVICES = "home_services"
    DAILY_WAGE = "daily_wage"
    DOMESTIC_WORK = "domestic_work"
    OTHER = "other"


WORK_TYPE_NAMES: dict[WorkType, str] = {
    WorkType.DELIVERY: "Delivery partner",
    WorkType.RIDE_HAILING: "Ride-hailing driver",
    WorkType.BIKE_TAXI: "Bike taxi rider",
    WorkType.HOME_SERVICES: "Home services",
    WorkType.DAILY_WAGE: "Daily-wage worker",
    WorkType.DOMESTIC_WORK: "Domestic worker",
    WorkType.OTHER: "Other work",
}

# App-based (platform) work recognised as gig/platform work under the Code on Social Security.
PLATFORM_WORK_TYPES: frozenset[WorkType] = frozenset(
    {WorkType.DELIVERY, WorkType.RIDE_HAILING, WorkType.BIKE_TAXI, WorkType.HOME_SERVICES}
)

WorkTypes = Annotated[list[WorkType], Field(max_length=len(WorkType))]


def unique_work_types(values: list[WorkType]) -> list[WorkType]:
    """De-duplicate while keeping order."""
    return list(dict.fromkeys(values))


class IssueType(StrEnum):
    """Grievance categories."""

    UNPAID_EARNINGS = "unpaid_earnings"
    ACCOUNT_DEACTIVATION = "account_deactivation"
    UNFAIR_PENALTY = "unfair_penalty"
    HARASSMENT = "harassment"
    ACCIDENT_CLAIM = "accident_claim"
    WAGE_THEFT = "wage_theft"


ISSUE_NAMES: dict[IssueType, str] = {
    IssueType.UNPAID_EARNINGS: "Unpaid or wrong payment of earnings",
    IssueType.ACCOUNT_DEACTIVATION: "Account deactivation / blocking",
    IssueType.UNFAIR_PENALTY: "Unfair penalty or fine",
    IssueType.HARASSMENT: "Harassment or abuse",
    IssueType.ACCIDENT_CLAIM: "Accident insurance / compensation claim",
    IssueType.WAGE_THEFT: "Wages not paid by contractor / employer",
}


class StateCode(StrEnum):
    """Indian states and union territories (ISO 3166-2:IN subdivision codes)."""

    AN = "AN"
    AP = "AP"
    AR = "AR"
    AS = "AS"
    BR = "BR"
    CH = "CH"
    CT = "CT"
    DH = "DH"
    DL = "DL"
    GA = "GA"
    GJ = "GJ"
    HP = "HP"
    HR = "HR"
    JH = "JH"
    JK = "JK"
    KA = "KA"
    KL = "KL"
    LA = "LA"
    LD = "LD"
    MH = "MH"
    ML = "ML"
    MN = "MN"
    MP = "MP"
    MZ = "MZ"
    NL = "NL"
    OR = "OR"
    PB = "PB"
    PY = "PY"
    RJ = "RJ"
    SK = "SK"
    TG = "TG"
    TN = "TN"
    TR = "TR"
    UP = "UP"
    UT = "UT"
    WB = "WB"


def today_ist() -> date:
    """Today's date in India, so 'today' is never rejected as future for Indian users."""
    return datetime.now(IST).date()


def _not_future(value: date) -> date:
    if value > today_ist():
        raise ValueError("date cannot be in the future")
    if value < EARLIEST_DATE:
        raise ValueError("date is too far in the past")
    return value


PastDate = Annotated[date, AfterValidator(_not_future)]
Money = Annotated[float, Field(ge=0, le=10_000_000)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class StrictModel(BaseModel):
    """Base for request models: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatTurn(StrictModel):
    """One previous message in a short conversation history."""

    role: ChatRole
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]


ChatHistory = Annotated[list[ChatTurn], Field(max_length=10)]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=1000)]
