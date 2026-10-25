"""Shared extended company fields and lossless mapping from bank profile features."""
from math import isfinite
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Presence = Literal["unknown", "yes", "no"]

# source feature -> editable profile field; flags remain distinct from interests.
BANK_FIELDS = {
    "ORG_TYPE": "organization_type", "OKFS_NAME": "ownership_form",
    "COMPANY_AGE_YEARS_GRADATION": "company_age_band", "NEGATIVEMARK": "key_owner_mark",
    "REVENUE_COMPANY": "annual_revenue", "GROUP_ID": "group_id",
    "AMOUNT_WORKERS_FNS_GRADATION": "employees_fns_band", "AMOUNT_WORKERS_SPARK_GRADATION": "employees_spark_band",
    "CL_TYPE": "client_type", "CL_PRODUCT_RKO_MARK": "product_rko", "CL_PRODUCT_GAR_MARK": "product_guarantees",
    "CL_PRODUCT_LOA_MARK": "product_credit", "CL_PRODUCT_LEA_MARK": "product_leasing",
    "CL_PRODUCT_DEP_MARK": "product_deposit", "CL_PRODUCT_ZPP_MARK": "product_payroll",
    "FACTORING_MARK": "product_factoring", "FTS_FLAG": "customs_activity", "FTS_COUNTRY_NAME": "customs_countries",
    "cohort_share": "cohort_revenue_share", "group_size": "group_size", "group_rank": "group_revenue_rank",
    "related_persons": "related_person_count", "okved_theme": "okved_theme",
}
PRESENCE_FIELDS = {"key_owner_mark", "product_rko", "product_guarantees", "product_credit", "product_leasing",
                   "product_deposit", "product_payroll", "product_factoring", "customs_activity"}
NUMERIC_FIELDS = {"annual_revenue", "cohort_revenue_share", "group_size", "group_revenue_rank", "related_person_count"}


class ProfileCounterparty(BaseModel):
    model_config = {"extra": "forbid"}
    client_id: str = Field(min_length=1, max_length=200)
    direction: Literal["in", "out"] = "out"
    pay_amt: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    pay_cnt: int | None = Field(default=None, ge=0, strict=True)
    same_group: bool | None = None

    @field_validator("client_id")
    @classmethod
    def normalize_client_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Укажите название или идентификатор контрагента")
        return value


class ExtendedProfile(BaseModel):
    organization_type: str = Field(default="", max_length=200)
    ownership_form: str = Field(default="", max_length=200)
    company_age_band: str = Field(default="", max_length=100)
    client_type: str = Field(default="", max_length=100)
    key_owner_mark: Presence = "unknown"
    annual_revenue: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    employees_fns_band: str = Field(default="", max_length=100)
    employees_spark_band: str = Field(default="", max_length=100)
    group_id: str = Field(default="", max_length=200)
    group_size: int | None = Field(default=None, ge=1, strict=True)
    group_revenue_rank: int | None = Field(default=None, ge=1, strict=True)
    related_person_count: int | None = Field(default=None, ge=0, strict=True)
    cohort_key: str = Field(default="", max_length=100)
    cohort_revenue_share: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    okved_theme: str = Field(default="", max_length=200)
    product_rko: Presence = "unknown"
    product_guarantees: Presence = "unknown"
    product_credit: Presence = "unknown"
    product_leasing: Presence = "unknown"
    product_deposit: Presence = "unknown"
    product_payroll: Presence = "unknown"
    product_factoring: Presence = "unknown"
    customs_activity: Presence = "unknown"
    customs_countries: str = Field(default="", max_length=1500)
    counterparties: list[ProfileCounterparty] = Field(default_factory=list, max_length=5000)

    @field_validator("counterparties")
    @classmethod
    def unique_connections(cls, values: list[ProfileCounterparty]) -> list[ProfileCounterparty]:
        keys = [(item.client_id, item.direction) for item in values]
        if len(keys) != len(set(keys)):
            raise ValueError("Контрагент и направление платежей не должны повторяться")
        return values


def extended_seed(original) -> dict:
    values = {feature.id: feature.value for feature in original.features}
    seeded = {}
    for source, key in BANK_FIELDS.items():
        if source not in values:
            continue
        value = values[source]
        if key in PRESENCE_FIELDS:
            seeded[key] = {"есть": "yes", "нет": "no"}.get(value, "unknown")
        elif key in NUMERIC_FIELDS:
            try:
                number = float(value)
                if isfinite(number):
                    seeded[key] = number if key in {"annual_revenue", "cohort_revenue_share"} else int(number)
            except (ValueError, TypeError):
                continue
        else:
            seeded[key] = value
    seeded["cohort_key"] = original.cohort_key or ""
    seeded["counterparties"] = [item.model_dump() for item in original.counterparties]
    return ExtendedProfile.model_validate(seeded).model_dump()


def extended_evidence(profile: dict) -> dict:
    """Include only provided fields. Unknown and missing numbers must not become zero."""
    evidence = {key: value for key, value in profile.items() if key in ExtendedProfile.model_fields
                and value not in (None, "", "unknown", [])}
    rows = evidence.get("counterparties", [])
    if rows:
        # Keep the full graph in storage and the editor; bound only model context.
        evidence["counterparty_summary"] = {
            "total": len(rows), "incoming": sum(row["direction"] == "in" for row in rows),
            "outgoing": sum(row["direction"] == "out" for row in rows),
            "same_group": sum(row.get("same_group") is True for row in rows),
            "shown": min(20, len(rows)),
        }
        evidence["counterparties"] = sorted(rows, key=lambda row: -(row.get("pay_amt") or 0))[:20]
    return evidence
