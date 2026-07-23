"""Mock workforce-analytics "data warehouse": a small synthetic dataset backing
a few pre-vetted, parameterized query templates — deliberately not free-form
SQL/text-to-SQL. Swapping in a real warehouse later means replacing the body
of run_query() with real queries against Snowflake/BigQuery/etc.; the
query_name/department_filter calling convention stays the same.
"""

from datetime import date

_DEPARTMENTS = ["Engineering", "Product", "People", "Sales", "Marketing"]

_EMPLOYEES = [
    {"department": "Engineering", "hire_date": date(2019, 1, 8), "pto_days_used_last_90d": 3},
    {"department": "Engineering", "hire_date": date(2022, 3, 14), "pto_days_used_last_90d": 2},
    {"department": "Engineering", "hire_date": date(2021, 6, 1), "pto_days_used_last_90d": 5},
    {"department": "Engineering", "hire_date": date(2023, 11, 20), "pto_days_used_last_90d": 1},
    {"department": "Engineering", "hire_date": date(2020, 9, 3), "pto_days_used_last_90d": 4},
    {"department": "Engineering", "hire_date": date(2024, 2, 12), "pto_days_used_last_90d": 0},
    {"department": "Product", "hire_date": date(2023, 7, 1), "pto_days_used_last_90d": 6},
    {"department": "Product", "hire_date": date(2021, 4, 18), "pto_days_used_last_90d": 3},
    {"department": "Product", "hire_date": date(2022, 10, 9), "pto_days_used_last_90d": 2},
    {"department": "People", "hire_date": date(2018, 5, 21), "pto_days_used_last_90d": 1},
    {"department": "People", "hire_date": date(2020, 1, 15), "pto_days_used_last_90d": 4},
    {"department": "Sales", "hire_date": date(2022, 8, 2), "pto_days_used_last_90d": 7},
    {"department": "Sales", "hire_date": date(2023, 3, 27), "pto_days_used_last_90d": 5},
    {"department": "Sales", "hire_date": date(2021, 12, 5), "pto_days_used_last_90d": 3},
    {"department": "Marketing", "hire_date": date(2022, 2, 14), "pto_days_used_last_90d": 2},
    {"department": "Marketing", "hire_date": date(2024, 5, 6), "pto_days_used_last_90d": 0},
]

QUERY_NAMES = ("headcount_by_department", "average_tenure_by_department", "pto_usage_trend")


def _filtered(department_filter: str | None) -> list[dict]:
    if not department_filter:
        return _EMPLOYEES
    return [e for e in _EMPLOYEES if e["department"].lower() == department_filter.lower()]


def _tenure_months(hire_date: date, today: date) -> float:
    return (today - hire_date).days / 30.44


def run_query(query_name: str, department_filter: str | None = None) -> dict:
    if query_name not in QUERY_NAMES:
        return {"error": "unknown_query", "message": f"'{query_name}' is not a recognized query."}

    rows = _filtered(department_filter)
    if not rows:
        return {"error": "no_data", "message": f"No data for department '{department_filter}'."}

    today = date.today()

    if query_name == "headcount_by_department":
        counts: dict[str, int] = {}
        for e in rows:
            counts[e["department"]] = counts.get(e["department"], 0) + 1
        return {"query": query_name, "result": counts}

    if query_name == "average_tenure_by_department":
        totals: dict[str, list[float]] = {}
        for e in rows:
            totals.setdefault(e["department"], []).append(_tenure_months(e["hire_date"], today))
        return {
            "query": query_name,
            "result": {dept: round(sum(months) / len(months), 1) for dept, months in totals.items()},
        }

    # pto_usage_trend: total PTO days used in the last 90 days, department-filterable
    total_days = sum(e["pto_days_used_last_90d"] for e in rows)
    return {
        "query": query_name,
        "result": {
            "window": "last_90_days",
            "department_filter": department_filter,
            "total_pto_days_used": total_days,
            "employee_count": len(rows),
        },
    }
