-- Tax Mate (ITR-2) — minimal PostgreSQL schema.
-- Just the computed filing itself.
--
-- Run with: psql -U <user> -d <database> -f database/schema.sql

CREATE TABLE filings (
    id               SERIAL PRIMARY KEY,
    assessment_year  VARCHAR(20) NOT NULL,
    regime           VARCHAR(3)  NOT NULL CHECK (regime IN ('old', 'new')),
    taxable_income   NUMERIC     NOT NULL DEFAULT 0,
    tax_payable      NUMERIC     NOT NULL DEFAULT 0
);
