"""
Minimal Postgres write path — just the `filings` table (database/schema.sql).
One function, called once per successful ITR-2 pipeline run. No connection
pool, no reads, no update-field syncing — this is intentionally as small as
the schema it writes to.
"""

import os
import psycopg2

_DATABASE_URL = os.environ["DATABASE_URL"]


def save_filing(itr2_form: dict) -> None:
    tax_computation = itr2_form.get("tax_computation", {}) or {}

    conn = psycopg2.connect(_DATABASE_URL)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO filings (assessment_year, regime, taxable_income, tax_payable)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    itr2_form.get("ay"),
                    tax_computation.get("regime"),
                    tax_computation.get("taxable_income", 0),
                    tax_computation.get("tax_payable", 0),
                ),
            )
    finally:
        conn.close()
