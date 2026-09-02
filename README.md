# TaxMate — Complete Project Documentation

**What it is:** An AI-powered system that reads your tax documents (Form 16, bank statements, capital gains statements, property/home loan certificates, insurance premium receipts, AIS/26AS/TIS, foreign income disclosures — PDF, Excel, or legacy .xls), automatically fills every field of your return — salary, house property (including multiple properties), other sources, capital gains, and foreign income (Schedule FSI, for Resident filers) — compares old vs new tax regime, validates the filled return for errors, explains the computation in plain English, and lets you ask questions about your taxes through a chat interface grounded in official CBDT sources.

**Who it is for:** Resident individuals filing for AY 2026-27, with any combination of salary, house property (any number of properties), other sources, capital gains (equity/non-equity, Sec 111A/112/112A), and foreign income/assets (computed via Schedule FSI with foreign tax credit under Sec 90/91). Non-Resident/RNOR filers are explicitly out of scope — the eligibility router flags and redirects them rather than silently mis-computing their return — see [Limitations](#9-limitations).

---

## Table of Contents

1. [How the whole system works — user flow](#1-how-the-whole-system-works)
2. [Architecture — all services](#2-architecture)
3. [Complete file structure with descriptions](#3-complete-file-structure)
4. [The RAG knowledge base — what goes into it](#4-the-rag-knowledge-base)
5. [The agent pipeline — LangGraph step by step](#5-the-agent-pipeline)
6. [The document parsers](#6-the-document-parsers)
7. [Setup — exact steps to run](#7-setup-exact-steps)
8. [What you need to provide](#8-what-you-need-to-provide)
9. [Limitations — what it does and does not do](#9-limitations)
10. [Interview cheat sheet](#10-interview-cheat-sheet)

---

## 1. How the whole system works

Here is the complete user flow from opening the browser to getting a filled return:

**Step 1 — Upload documents.** The user goes to `http://localhost:3000/upload-itr2` and drags in their documents — Form 16, bank statements, AIS/26AS/TIS, capital gains statements, property/home loan certificates, insurance premiums, foreign income disclosures, residential-status declarations. PDF, `.xlsx`/`.xlsm`, and legacy `.xls` are all accepted. The frontend sends these to the API gateway at port 3001, which forwards them to the Doc Parser service at port 8002.

**Step 2 — Document parsing.** The Doc Parser auto-detects the document type from its content (`POST /parse/auto`) and routes it to one of 13 specialized parsers in `doc-parser/parsers/`. For Form 16, it extracts gross salary, HRA exemption, standard deduction, professional tax, every deduction claimed (80C, 80D, 80CCD), and total TDS deducted. For bank statements it finds the transaction table, classifies each row as salary/savings interest/FD interest/TDS, and totals them up. For AIS/Form 26AS/TIS it cross-checks TDS credits and flags any discrepancy between what was deducted and what was actually deposited with the government. For capital gains, property, and foreign income documents it uses an LLM-assisted extraction pass rather than pure regex, since these statements vary far more in layout than Form 16. Each parser returns a structured JSON object.

**Step 3 — Agent pipeline.** The parsed documents first pass through the eligibility router (`agent-orchestrator/graph/router.py`), which flags and redirects Non-Resident/RNOR filings — this codebase only computes returns for Resident and Ordinarily Resident filers. In-scope filings are sent to the Agent Orchestrator at port 8000, which runs a 5-node LangGraph pipeline (`graph/itr2_graph.py`):

- **fill_form node** — maps every extracted field onto the return schema. Form 16 isn't mandatory — a filer whose income is entirely foreign, property, or capital-gains based still gets a computed return, with a flagged placeholder for any field (like name) that had no other source. Also flags when foreign income is present but residential status wasn't confirmed, or when foreign income is present without an accompanying foreign asset disclosure.
- **compare_regimes node** — computes tax under the regime the source documents indicate (deterministic slab math, no LLM), then separately computes the other regime purely for a side-by-side comparison display, and reports the lower one as a recommendation.
- **validate node** — checks required fields are present, exemptions/TDS sanity against gross salary, regime consistency (e.g. flags 80C claimed under the new regime, which is ignored there), house-property loss sign, capital-gains bucket sign, a disclosure warning when non-equity LTCG is taxed flat without checking the pre-23-Jul-2024 indexation election, and gross-vs-taxable income consistency. Each violation becomes a flag with severity (error/warning) and a suggestion.
- **score_confidence node** — starts at a base score of 0.95 and subtracts 0.3 per validation error and 0.05 per warning, floored at 0.1.
- **explain node** — builds a structured plain-English summary (gross total income, capital gains by section, foreign income and foreign tax credit, taxable income, total tax liability, TDS, refund/payable, and a regime recommendation when there's a saving) directly from the already-computed numbers. This node is deterministic string formatting, not an LLM call — the LLM is used elsewhere (chat, and the LLM-assisted parsers), not here.

**Step 4 — Form viewer.** The filled form is shown at `/form-itr2?session=SESSION_ID`. Every field shows its value, a colour-coded confidence bar (green ≥ 80%, amber ≥ 50%, red below), and the source badge (Form 16 / Bank stmt / Computed / Manual). Validation flags appear as banners at the top. The user can click the edit button on any field, correct the value, and save — which sets that field's confidence to 100% (human verified) and logs it in the audit trail.

**Step 5 — Chat.** The user can go to `/chat?session=SESSION_ID` and ask any tax question in natural language. The question goes to the RAG service at port 8001, which embeds it, retrieves the 5 most relevant chunks from the FAISS vector store using MMR diversity filtering, reranks them with a cross-encoder, and sends them with the question to the LLM (Groq → OpenRouter → OpenAI fallback chain). The answer is returned with source citations linking back to the exact CBDT page or document (served via `GET /api/pdfs/:filename` for local PDFs). The chat is aware of the user's filled form if they are in a session (it knows their income, regime, and taxable income).

**Step 6 — Export.** The user clicks "Export JSON" to download the completely filled return as a JSON file, which can be imported into the ITD offline utility or used to pre-fill the online portal.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend  Next.js · React · Tailwind  :3000                │
│  /upload-itr2   /form-itr2?session=...   /chat?session=...  │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP
┌──────────────────────▼──────────────────────────────────────┐
│  API Gateway  Node.js / Express  :3001                      │
│  Auth · Rate limiting · File proxying · Service routing     │
└────────┬──────────────────┬─────────────────┬──────────────┘
         │                  │                 │
┌────────▼──────────┐┌──────▼──────────┐┌──────▼─────────────┐
│ Doc Parser         ││  RAG Service    ││ Agent Orchestrator  │
│ Python/FastAPI     ││  Python/FastAPI ││ Python/FastAPI      │
│ :8002              ││  :8001          ││ :8000               │
│                    ││                 ││                     │
│ 13 parsers:        ││  FAISS index    ││  graph/router.py    │
│ form16, bank_stmt, ││  MMR retrieval  ││  screens Non-        │
│ ais, form26as, tis,││  cross-encoder  ││  Resident/RNOR out   │
│ capital_gains,     ││                 ││  of scope, then a    │
│ property, foreign_ ││                 ││  5-node LangGraph    │
│ income, health/    ││                 ││  pipeline:           │
│ life_insurance,    ││                 ││  fill_form           │
│ home_loan, other_  ││                 ││  compare_regimes     │
│ sources_income,    ││                 ││  validate            │
│ residential_status ││                 ││  score_confidence    │
│                    ││                 ││  explain             │
└────────┬───────────┘└──────┬──────────┘└──────┬───────────────┘
         │                   │                  │
         └───────────────────┴──────────────────┴──────────────┐
                                                                 │
┌────────────────────────────────────────────────────────────▼─┐
│  Shared Python  (imported by all 3 Python services)           │
│  shared/itr2_schema.py — Pydantic models for the full return  │
│  shared/tax_utils_itr2.py — slab/regime math, capital gains,  │
│    foreign tax credit                                         │
│  shared/tax_engine/  — config-driven primitives (capital      │
│    gains, foreign income aggregation, FTC), per-AY configs    │
│  shared/validator_itr2.py — cross-field validation checks     │
│  shared/llm_client.py — unified LLM client: tries Groq, then  │
│    OpenRouter, then OpenAI, in that order, per call            │
└───────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  Infrastructure                                         │
│  PostgreSQL :5432  · Redis :6379                        │
│  Docker Compose ties everything together                │
└─────────────────────────────────────────────────────────┘
```

**Why microservices:**
- Each service scales differently. Doc Parser runs only on document upload. RAG runs on every chat query. Agent runs on pipeline trigger.
- Python for ML (pdfplumber, FAISS, LangChain, sentence-transformers). Node.js for async I/O coordination.
- AY updates only redeploy the RAG service — no other service touched.

**Why a router:** The eligibility router is the single place that screens Non-Resident/RNOR filings out of scope — this codebase only computes returns for Resident and Ordinarily Resident filers — before the pipeline runs, keeping that check out of the pipeline nodes themselves.

**Why a fallback chain instead of one LLM provider:** Groq and OpenRouter both offer free tiers with OpenAI-compatible APIs, so `shared/llm_client.py` tries them first and only falls back to paid OpenAI (`gpt-4o-mini`) if both fail or no key is set — the whole system can run on $0 in API cost for most users.

---

## 3. Complete file structure

Every file in the project, what it does, and why it exists.

```
TaxMate/
│
├── .env.example                 Configuration template. Copy to .env and fill in.
├── docker-compose.yml           Starts all 7 services in correct order with health checks.
├── pytest.ini                   Tells pytest where tests live and default flags.
│
├── knowledge-base/              Everything needed to build the FAISS vector store.
│   ├── requirements.txt         Packages for running kb scripts locally (pdfplumber, faiss, etc)
│   ├── scraper.py               Scrapes official/reference websites using headless Playwright +
│   │                            BS4. Outputs markdown chunks to rag_output/chunks/. Handles
│   │                            JS-heavy sites (ClearTax is React, incometax.gov.in is Drupal).
│   ├── build_itr2_kb.py         Ingests capital-gains, house-property, and foreign-income
│   │                            reference sources → rag_output/itr2/. See §4 for why this is a
│   │                            separate script rather than scraper.py.
│   ├── embedder.py              Takes all_chunks.jsonl → embeds → saves FAISS index.
│   │                            Two backends: HuggingFace BGE (free) or OpenAI (better quality).
│   │                            Supports --form-type itr2 to build the ITR-2 namespace.
│   ├── retriever.py             Standalone retriever class: MMR + cross-encoder reranking.
│   │                            Importable into any Python service. Also has a CLI for testing.
│   ├── manual_fallback.py       Fallback scraper using curl when Playwright gets blocked.
│   │                            Also handles manually saved HTML files from browser.
│   ├── pdf_ingester.py          Reads your downloaded PDFs from knowledge-base/pdfs/.
│   │                            Extracts text (pdfplumber + PyMuPDF), cleans, chunks at
│   │                            512 tokens, writes to rag_output/chunks/. Run AFTER scraper.py,
│   │                            then run embedder.py to include PDFs in FAISS. Supports
│   │                            --form-type itr2 for PDFs dropped in pdfs/itr2/.
│   ├── itr_form_schema_loader.py Reads the official ITD JSON schema + Excel field map you
│   │                            downloaded from the ITD utility. Maps every official field name
│   │                            to the internal schema dot-path. Outputs field_map.json.
│   ├── verify_itr2_retrieval.py Sanity-checks the built ITR-2 index against known-answer queries.
│   ├── build_page_map.py, patch_ay_labels.py, patch_meta.py
│   │                            One-off maintenance scripts used when CBDT page structure or
│   │                            AY labelling in existing chunk metadata needed correcting
│   │                            in place, without a full re-scrape.
│   ├── pdfs/                    PUT YOUR DOWNLOADED PDFS HERE (pdfs/itr2/ for capital-gains/
│   │   │                        house-property/foreign-income-specific PDFs).
│   │   ├── itr_instructions_AY2026-27.pdf
│   │   ├── circular_03_2025.pdf
│   │   ├── income_tax_act_sections.pdf
│   │   ├── income_tax_rules_2026.pdf
│   │   └── ... (any other PDFs)
│   ├── form_files/              PUT YOUR DOWNLOADED FORM FILES HERE.
│   │   ├── itr_schema_AY2026-27.json   (official JSON schema from ITD utility)
│   │   └── itr_fields_AY2026-27.xlsx  (Excel field map)
│   └── vector_store/             FAISS indexes, one namespace per AY (+ "_ITR2" suffix for the
│                                 capital-gains/foreign-income-only namespace). See §4.
│
├── shared/                      Python package imported by all 3 Python services.
│   ├── __init__.py
│   ├── itr2_schema.py           BACKBONE OF THE PROJECT. Pydantic models for the full return:
│   │                            PersonalInfo, SalaryIncome (Schedule S), multi-property
│   │                            HousePropertyIncome (Schedule HP), OtherSourcesIncome
│   │                            (Schedule OS), Deductions (Chapter VI-A with all sections),
│   │                            TDSEntry, TaxComputation, FieldConfidence, ValidationFlag,
│   │                            plus CapitalGains (Schedule CG) and ForeignIncomeEntry /
│   │                            ForeignAssetEntry (Schedule FSI/FA) — all rolled into ITR2Form.
│   ├── tax_utils_itr2.py        All statutory tax math. Contains:
│   │                            - AY-specific slab rates, rebate limits, cess rate, surcharge
│   │                              slabs for both regimes, driven by shared/tax_engine's configs
│   │                            - compute_tax_from_engine_itr2() — runs the config-driven
│   │                              engine and reshapes the result, including capital_gains and
│   │                              house_property_loss_carried_forward
│   │                            - Capital gains rates (Sec 111A/112/112A), Schedule FSI
│   │                              foreign tax credit math
│   │                            All functions are pure Python, no LLM, deterministic, tested.
│   ├── tax_engine/               Config-driven computation primitives:
│   │   ├── interpreter.py       capital gains computation, foreign income aggregation,
│   │   ├── primitives.py        foreign tax credit application — plus per-AY JSON configs
│   │   └── configs/             so slab/rate changes for a new AY live in one place.
│   ├── validator_itr2.py        Cross-field validation: required fields present, exemptions/
│   │                            TDS sanity vs gross salary, regime consistency (flags 80C
│   │                            claimed under the new regime), house-property loss sign,
│   │                            capital-gains bucket sign checks, a disclosure warning for
│   │                            non-equity LTCG taxed without checking the pre-23-Jul-2024
│   │                            indexation election, and gross-vs-taxable income consistency.
│   └── llm_client.py            Unified LLM client used by every LLM call in the codebase
│                                (LLM-assisted parsers, RAG answer generation).
│                                Tries Groq (llama-3.3-70b, free tier) → OpenRouter (free-tier
│                                llama/deepseek models) → OpenAI (gpt-4o-mini, paid) in order,
│                                stopping at the first provider with a working key. See §2.
│
├── doc-parser/                  Python microservice. "Universal Document Parser" — accepts
│   │                            PDF, .xlsx/.xlsm, and legacy .xls, returns structured JSON.
│   ├── __init__.py
│   ├── Dockerfile
│   ├── requirements.txt         pdfplumber, fastapi, uvicorn, python-multipart, pillow,
│   │                            openpyxl (.xlsx/.xlsm), xlrd (legacy .xls)
│   ├── main.py                  FastAPI app. Endpoints:
│   │                            POST /parse/auto           — auto-detects doc type from content
│   │                                                          (keyword signatures per type) and
│   │                                                          dispatches to the matching parser
│   │                                                          below; this is what the frontend
│   │                                                          uses for every upload
│   │                            POST /parse                — alias for /parse/auto
│   │                            POST /parse/form16          — Form 16 specifically
│   │                            POST /parse/bank-statement  — bank statement specifically
│   │                            GET  /health
│   │                            AIS/Form 26AS/TIS parsing is invoked internally from within
│   │                            /parse/auto — there is no standalone /parse/ais route.
│   │                            A document whose text matches a foreign-currency signal
│   │                            (currency codes, "Exchange Rate") gets reclassified as
│   │                            foreign_income even if uploaded to a different slot, so it
│   │                            isn't silently mis-parsed as ₹0 — see main.py's
│   │                            FOREIGN_CURRENCY_SIGNAL_PATTERN.
│   └── parsers/                 13 parsers, each returning a structured dict + parse_confidence:
│       ├── __init__.py
│       ├── _llm_common.py       Shared helper for the LLM-assisted parsers below (prompt
│       │                        construction, JSON-mode calls via shared/llm_client.py,
│       │                        chunking for large documents so extraction doesn't fail on
│       │                        long statements).
│       ├── form16.py            Form 16 parser. Uses regex patterns matching TRACES standard
│       │                        format (the format CBDT mandates all employers use). Extracts:
│       │                        employer TAN/PAN, employee PAN, assessment year, gross salary,
│       │                        Sec 17(1)/(2)/(3) breakdown, all Sec 10 exempt allowances
│       │                        (HRA 10(13A), LTA 10(10)), Sec 16 deductions (standard, prof tax),
│       │                        all Chapter VI-A deductions claimed, TDS by quarter, rebate 87A.
│       │                        Falls back to pdfplumber table extraction if regex misses fields.
│       │                        Returns parse_confidence (0–1) and warnings list.
│       ├── bank_statement.py    Bank statement parser. Detects bank by header patterns (SBI,
│       │                        HDFC, ICICI, Axis). Finds transaction table via pdfplumber.
│       │                        Classifies each row: salary / interest_savings / interest_fd /
│       │                        interest_rd / tax_deducted / other. Aggregates by category.
│       │                        Savings interest → 80TTA. FD interest → other sources income.
│       │                        Bank TDS → Schedule TDS2. Returns totals + transaction list.
│       ├── ais.py               AIS parser. Reads the TDS credit table by deductor. Separates
│       │                        Sec 192 (salary TDS) from Sec 194A (interest TDS).
│       │                        reconcile_form16_vs_ais() cross-checks Form 16 TDS vs AIS TDS
│       │                        and returns a mismatch report used by the validator.
│       ├── form26as.py          Form 26AS parser — the older TDS/tax-credit statement format,
│       │                        parsed separately from AIS since the two documents differ in
│       │                        layout even though they cover overlapping data.
│       ├── tis.py               Taxpayer Information Summary parser — the "Accepted by
│       │                        Taxpayer" reconciled view that sits alongside AIS.
│       ├── capital_gains.py     LLM-assisted extraction from broker/CAMS capital gains
│       │                        statements — accepts PDF and Excel (incl. legacy .xls, the
│       │                        common format for older broker exports). Classifies each
│       │                        realized gain under Sec 111A (equity STCG) / 112 (non-equity
│       │                        LTCG) / 112A (equity LTCG).
│       ├── property.py          Extracts Schedule HP fields (annual value, municipal tax, rent
│       │                        received, tenant details) for self-occupied, let-out, and
│       │                        deemed-let-out properties — including multiple properties in
│       │                        one filing.
│       ├── foreign_income.py    Extracts Schedule FSI/FA fields (foreign income, foreign
│       │                        assets, Form 67 foreign tax credit) for Resident filers.
│       ├── health_insurance.py  Extracts premium paid for Sec 80D.
│       ├── life_insurance.py    Extracts premium paid for Sec 80C.
│       ├── home_loan.py         Extracts principal repaid (80C) and interest paid (Sec 24b /
│       │                        Schedule HP) from the bank's provisional certificate.
│       ├── other_sources_income.py  Dividend and interest income not already covered by the
│       │                        bank statement parser (e.g. a separate dividend statement).
│       └── residential_status.py  Extracts days-in-India / Sec 6 residential status signals,
│                                consumed by the router to flag NR/RNOR filings as out of scope.
│
├── rag-service/                 Python microservice. Answers tax questions using FAISS + LLM.
│   ├── __init__.py
│   ├── Dockerfile
│   ├── requirements.txt         fastapi, sentence-transformers, faiss-cpu, openai
│   └── main.py                  FastAPI app. Endpoints:
│                                POST /query  — full RAG pipeline: embed → MMR → rerank → LLM
│                                POST /query/chunks — return raw chunks without LLM (debug)
│                                GET  /indexes — list available AY namespaces
│                                GET  /health
│                                Loads the FAISS index for DEFAULT_AY (env var, e.g. AY2026-27)
│                                on startup. Uses sentence-transformers BGE model to embed
│                                queries (same model used by embedder.py — MUST match). MMR
│                                retrieval with lambda=0.6, cross-encoder reranking with
│                                ms-marco-MiniLM. shared/llm_client.py generates the final
│                                answer from retrieved context (Groq → OpenRouter → OpenAI).
│
├── agent-orchestrator/          Python microservice. Runs the LangGraph tax computation
│   │                            pipeline.
│   ├── __init__.py
│   ├── Dockerfile
│   ├── requirements.txt         fastapi, langgraph, langchain, langchain-openai, httpx
│   ├── main.py                  FastAPI app. Endpoints:
│   │                            POST /pipeline/run    — screens eligibility via
│   │                                                     graph/router.py (Non-Resident/RNOR
│   │                                                     filings are redirected out of scope),
│   │                                                     then runs the pipeline
│   │                            GET  /pipeline/session/{id} — get filled form for session
│   │                            POST /pipeline/update-field — manual field correction
│   │                            POST /chat/query      — Q&A (proxies to RAG service)
│   │                            GET  /pipeline/export/{id} — download filled form JSON
│   │                            GET  /health
│   │                            Sessions stored in-memory dict (replace with Redis in prod).
│   └── graph/
│       ├── __init__.py
│       ├── router.py            Eligibility router — the ONLY place that decides whether a
│       │                        filing is out of scope entirely (Non-Resident/RNOR residential
│       │                        status, computed from residential_status.py's extraction).
│       │                        In-scope filings proceed to the pipeline below.
│       └── itr2_graph.py        THE HEART OF THE PROJECT. LangGraph state machine with 5 nodes.
│                                AgentState TypedDict holds everything across nodes.
│                                node_fill_form: maps parsed docs onto the return schema — Form
│                                  16 isn't mandatory (a filer with only foreign/property/
│                                  capital-gains income still gets a computed return, with a
│                                  flagged placeholder for any field with no other source);
│                                  flags foreign income lacking a confirmed residential status
│                                  or an accompanying foreign asset disclosure.
│                                node_compare_regimes: computes tax under the regime the source
│                                  documents indicate, then separately computes the other regime
│                                  purely for a side-by-side comparison, reporting the lower one
│                                  as a recommendation.
│                                node_validate: runs the cross-field checks described in
│                                  validator_itr2.py above, creates ValidationFlag objects with
│                                  severity and a suggestion for each issue.
│                                node_score_confidence: base score 0.95, − 0.3 per error,
│                                  − 0.05 per warning, floored at 0.1.
│                                node_explain: builds a structured plain-English summary
│                                  (income, capital gains by section, foreign income + FTC, tax
│                                  liability, refund/payable, regime recommendation) directly
│                                  from the computed numbers — deterministic string formatting,
│                                  not an LLM call.
│                                run_itr2_pipeline(): convenience function that builds and
│                                  invokes the compiled graph with an initial state.
│
├── api-gateway/                 Node.js microservice. The single entry point for the frontend.
│   ├── Dockerfile
│   ├── package.json             express, multer, cors, express-rate-limit, jsonwebtoken, node-fetch
│   └── src/
│       └── index.js             Express app. Routes:
│                                POST /api/upload/:docType → doc-parser (multipart/form-data proxy)
│                                POST /api/pipeline/run  → agent-orchestrator
│                                GET  /api/pipeline/:id  → agent-orchestrator
│                                POST /api/pipeline/update-field → agent-orchestrator
│                                GET  /api/pipeline/export/:id  → agent-orchestrator
│                                POST /api/chat           → agent-orchestrator → rag-service
│                                GET  /api/pdfs/:filename → serves a knowledge-base PDF so chat
│                                                            source citations are clickable
│                                GET  /api/health         → aggregates all service health checks
│                                Multer handles file uploads in-memory (max 20MB, PDF/JPG/PNG/
│                                .xlsx/.xlsm/.xls).
│                                JWT auth middleware (SKIP_AUTH=true for local dev).
│                                Rate limiting: 100 req/15min general, 20 req/15min for uploads.
│
├── frontend/                    Next.js 14 / React / Tailwind.
│   ├── Dockerfile               Multi-stage: build → standalone output
│   ├── package.json             next, react, tailwindcss, typescript
│   ├── next.config.js           output: standalone (for Docker)
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── tsconfig.json
│   └── src/app/
│       ├── layout.tsx           Root layout wrapping all pages. Sets page title and metadata.
│       ├── globals.css          Tailwind base imports + body font.
│       ├── upload-itr2/
│       │   └── page.tsx         Document upload UI. DropZone components for Form 16, bank
│       │                        statements, capital gains statements, property/home loan
│       │                        certificates, foreign income, health/life insurance,
│       │                        other-sources income, residential status, and a
│       │                        reference-document slot for supporting context. Each drop zone
│       │                        calls /api/upload/:docType, shows parse confidence and
│       │                        warnings on the returned FileCard. "Fill My Return" button
│       │                        calls /api/pipeline/run with all parsed docs, then navigates
│       │                        to /form-itr2?session=SESSION_ID.
│       ├── form-itr2/
│       │   └── page.tsx         Filled form viewer. Loads session from /api/pipeline/:id.
│       │                        Shows every field grouped into section cards (Salary, House
│       │                        Property, Other Sources, Deductions, Capital Gains, Foreign
│       │                        Income, Tax Computation). Each FieldRow shows: label,
│       │                        explanation, confidence bar, source badge, value, edit button.
│       │                        Validation flags at top as banners. Regime recommendation card
│       │                        in green. EditModal for any field (calls
│       │                        /api/pipeline/update-field). Wrapped in Suspense.
│       └── chat/
│           └── page.tsx         Q&A chat interface. Calls /api/chat with the question.
│                                Shows RegimeCard (old vs new tax + saving) if session active.
│                                8 suggested questions shown before first message.
│                                Answers show with source citation links. Auto-scroll.
│                                Shift+Enter for newline, Enter to send. Wrapped in Suspense.
│
└── tests/                       Pytest test suite (6 documented files, 56 test functions).
    ├── __init__.py
    ├── conftest.py              Shared fixtures: sample Form 16 data, sample bank data.
    │                            Adds all service directories to sys.path.
    ├── test_tax_engine.py       7 tests for shared/tax_engine — the config-driven computation
    │                            primitives.
    ├── test_tax_engine_itr2.py  9 tests for tax_utils_itr2.py — capital gains rates, foreign
    │                            tax credit application.
    ├── test_capital_gains_parser.py  13 tests for parsers/capital_gains.py — Sec 111A/112/112A
    │                            classification, Excel and legacy .xls input handling.
    ├── test_validator_itr2.py   3 tests for shared/validator_itr2.py.
    ├── test_router.py           17 tests for graph/router.py — eligibility screening,
    │                            NR/RNOR out-of-scope detection, foreign-currency-signal
    │                            reclassification.
    └── test_rag_verification.py  7 tests sanity-checking retrieval quality against known-
                                 answer queries (companion to knowledge-base/verify_itr2_retrieval.py).
```

---

## 4. The RAG knowledge base

### What goes in

The FAISS vector store contains two types of content:

**1. Web content (from scraper.py)**

| URL | What it contains | Why it matters for RAG |
|-----|-----------------|------------------------|
| incometax.gov.in/help/individual/return-applicable-1 | Slab tables, surcharge rules, 87A rebate numbers | Exact numbers for regime comparison |
| cleartax.in/s/80c-80-deductions | Plain-English 80C guide | Edge case coverage |

**2. PDF content (from pdf_ingester.py)**

Your downloaded PDFs go in `knowledge-base/pdfs/`. The ingester auto-detects what each PDF is based on the filename using regex patterns. Recommended files:

```
itr_instructions_AY2026-27.pdf     ← CBDT instructions booklet (most important)
circular_03_2025.pdf               ← CBDT Circular 03/2025 (TDS on salary)
income_tax_act_sections.pdf        ← IT Act: Sec 80C, 80D, 87A, 111A, 112, 112A, 115BAC, etc.
income_tax_rules_2026.pdf          ← Income Tax Rules
```

**3. Official form files (from itr_form_schema_loader.py)**

Your downloaded official JSON schema and Excel field map go in `knowledge-base/form_files/`. The loader builds `field_map.json` which maps every official ITD field name (e.g. `Section80C`) to the internal schema path (e.g. `deductions.sec_80c`).

### How chunks are created

Chunking strategy (512 tokens, 64-token overlap):
- FAQ pages: split on Q&A pairs — each Q+A is one self-contained chunk
- User manuals: split on markdown headings — each section is one chunk
- PDFs: split on numbered section headings (e.g. "2.1", "PART A") — then slide a window if any section exceeds 512 tokens

Every chunk carries metadata: `source`, `doc_type`, `applicable_ay`, `section`, `url`. This metadata is stored alongside every FAISS vector so every retrieval result can cite its exact source.

### How retrieval works

1. User's question is embedded with the same model used to build the index (BGE-small-en)
2. FAISS flat-L2 search returns top-15 candidates
3. MMR (Maximum Marginal Relevance, λ=0.6) selects top-5 diverse results — prevents returning 5 identical chunks about the same topic
4. Cross-encoder `ms-marco-MiniLM-L-6-v2` reranks the 5 chunks for precision
5. `shared/llm_client.py` (Groq → OpenRouter → OpenAI) generates the answer using only the retrieved context (grounded, no hallucination)
6. Source URLs returned alongside the answer

### AY versioning

Each assessment year gets its own FAISS namespace. Currently built:
- `vector_store/AY2024-25.faiss` + `AY2024-25.meta.json`
- `vector_store/AY2026-27.faiss` + `AY2026-27.meta.json` (current AY, set as `DEFAULT_AY` for rag-service)
- `vector_store/AY2026-27_ITR2.faiss` + `AY2026-27_ITR2.meta.json` (see next section)

When new AY drops: ingest new PDFs → run embedder with `--ay <AY>` → only the RAG service redeploys. No other service is touched.

### ITR-2 namespace (capital gains + house property)

Same pipeline, a separate namespace: `AY2026-27_ITR2` (via `shared/tax_engine`'s `itr2_ay()` suffix convention — `{ay}_ITR2`, not a new naming scheme). Everything under this namespace stays scoped to capital gains (Sec 111A/112/112A) and multi-property house-property topics — the base namespace above covers the rest (salary, deductions, general filing procedure).

Ingestion is `knowledge-base/build_itr2_kb.py` rather than `scraper.py`, because two of its six sources need a different fetch path: incometax.gov.in is still server-rendered (plain `curl` + BeautifulSoup, same as the base scraper), but cleartax.in's capital-gains/house-property articles turned out to be a client-rendered Next.js SPA with no article content in the raw HTML at all — confirmed by inspection, not assumed. Their content is captured verbatim in the script instead (see its module docstring for the rationale, the same workaround `manual_fallback.py` already documents for anti-bot-blocked pages).

```
knowledge-base/build_itr2_kb.py    → rag_output/itr2/{raw,chunks,combined}/all_chunks.jsonl
knowledge-base/pdf_ingester.py --form-type itr2   → same output dir, for any PDFs dropped in pdfs/itr2/
knowledge-base/embedder.py --form-type itr2       → vector_store/AY2026-27_ITR2.faiss + .meta.json
knowledge-base/verify_itr2_retrieval.py           → sanity-checks the built index against 5 known-answer queries
```

Sources currently ingested (46 chunks total):

| Source | Covers |
|--------|--------|
| incometax.gov.in — ITR-2 User Manual & FAQs | Filing procedure, eligibility, required documents |
| cleartax.in/s/short-term-capital-gain-on-shares | Section 111A — equity STCG, 20% rate |
| cleartax.in/s/long-term-capital-gains-on-shares | Section 112A — equity LTCG, 12.5% rate, ₹1.25L exemption |
| cleartax.in/s/section-112-... | Section 112 — non-equity LTCG |
| cleartax.in/s/house-property | Schedule HP — self-occupied/let-out/deemed-let-out, multiple properties |

Rebuild from scratch: `python build_itr2_kb.py && python embedder.py --backend huggingface --form-type itr2 && python verify_itr2_retrieval.py`.

---

## 5. The agent pipeline

The LangGraph pipeline in `agent-orchestrator/graph/itr2_graph.py` is a state machine. Every node receives the full `AgentState` dict and returns only what it changes. LangGraph merges the return into the state automatically.

```
Parsed documents (Form 16, bank statement, capital gains, property,
foreign income, insurance, and other-sources JSON)
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ Node 1: fill_form                                            │
│                                                              │
│  Form 16 optional — if absent but other income docs exist,   │
│    a placeholder name/regime/TDS is used and flagged          │
│  Merge capital gains / property / foreign income documents    │
│    onto the return schema                                     │
│  If foreign income present without confirmed residential      │
│    status, or without a foreign asset disclosure → warn       │
│  Assign confidence (0–1) and source citation to each field    │
│  Compute derived values (net salary, 80TTA from interest)     │
│  Build TDSEntry objects                                       │
│  Compute gross total income                                   │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ Node 2: compare_regimes                                      │
│                                                              │
│  Compute tax under the regime the source documents indicate  │
│  (NOT LLM inference — deterministic slab math via             │
│  compute_tax_from_engine_itr2())                               │
│  Separately compute the other regime purely for a             │
│  side-by-side comparison display                              │
│  Report the lower one as a recommendation                     │
│  Fill tax_computation section of the return                   │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ Node 3: validate                                             │
│                                                              │
│  Required fields present (gross salary, standard deduction,  │
│    tax regime, TDS)                                          │
│  Exemptions vs gross salary / TDS vs salary sanity            │
│  Regime consistency (80C claimed under new regime → warn)     │
│  House-property loss carried forward can't be negative        │
│  Capital gains buckets can't be negative                      │
│  Non-equity LTCG taxed flat 12.5% without checking the        │
│    pre-23-Jul-2024 indexation election → disclosure warning   │
│  Gross total income vs taxable income consistency              │
│  Each flag has: field, severity, message, suggestion          │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ Node 4: score_confidence                                     │
│                                                              │
│  Base score 0.95                                              │
│  − 0.3 per validation error, − 0.05 per warning                │
│  Floored at 0.1                                                │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ Node 5: explain                                              │
│                                                              │
│  Deterministic template — NOT an LLM call:                   │
│  - Gross total income, house property loss carried forward   │
│  - Capital gains by section (111A / 112A / 112) + CG tax      │
│  - Foreign income (Schedule FSI) + foreign tax credit claimed │
│  - Taxable income, total tax liability                        │
│  - TDS deducted, refund due or tax payable                    │
│  - Regime recommendation with rupee saving, if any             │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
                  Output: filled ITR2Form
                  + confidence_scores dict
                  + validation_flags list
                  + regime_analysis dict
                  + explanations dict
                  + audit_trail list
```

---

## 6. The document parsers

This section covers three of the document parsers in detail — the ones almost every filing uses (salary, bank interest, tax-credit reconciliation). The doc-parser service has 13 parsers total; see the `doc-parser/parsers/` listing in §3 for the full set (capital gains, property, foreign income, insurance, home loan, other-sources income, residential status, Form 26AS, TIS).

### Form 16 parser

Form 16 is issued by the employer and comes in two formats:
- **TRACES-generated** (standard): predictable label positions. The parser uses regex patterns that match the exact label text mandated by CBDT (e.g. "Salary as per provisions contained in section 17(1)").
- **Employer-generated**: variable layout. Falls back to pdfplumber's table extraction.

The parser extracts from Part A: employer TAN/PAN, employee PAN, TDS by quarter.
From Part B: gross salary (17(1)+17(2)+17(3)), every Sec 10 exemption (HRA, LTA), Sec 16 deductions (standard deduction, professional tax), all VI-A deductions as declared by employer (80C, 80D, etc.), taxable income, total TDS.

Returns `parse_confidence` which is the fraction of critical fields successfully extracted. If confidence < 0.5 it warns that the PDF may be scanned and needs OCR.

### Bank statement parser

Detects bank by regex against the header text (SBI, HDFC, ICICI, Axis). Each bank has slightly different column ordering and date formats — the parser handles all of them via `col()` function that searches column headers by keyword.

Classifies each transaction row by regex matching against the description:
- Salary: "NEFT", "PAYROLL", "SAL", monthly credit from employer
- Savings interest: "INT CREDITED", "SB INT", "QUARTERLY INT"
- FD interest: "FD INT", "TERM DEPOSIT INT", "TDR INT"
- TDS: "TDS DEDUCTED", "TAX DEDUCTED AT SOURCE"

Total savings interest → goes to `other_sources.savings_bank_interest` → 80TTA capped at ₹10,000.
Total FD interest → goes to `other_sources.fd_interest` → fully taxable under Other Sources.
Bank TDS → goes to Schedule TDS2 (TDS on income other than salary).

### AIS / Form 26AS parser

Reads the TDS credit table (deductor-wise). Separates Sec 192 (salary TDS → Schedule TDS1) from Sec 194A (interest TDS → Schedule TDS2) and other sections.

The key function is `reconcile_form16_vs_ais()` which compares:
- TDS shown in Form 16 Part A
- TDS shown in AIS under Sec 192

If these differ by more than ₹100, the validator raises an error. This catches the common case where an employer deducts TDS but doesn't deposit it, which causes an income tax notice after filing.

---

## 7. Setup — exact steps

### Prerequisites

- Docker Desktop (for docker compose)
- Python 3.11+ (for running knowledge-base scripts locally)
- A free Groq or OpenRouter API key (for LLM answers) — OpenAI is supported too but costs money and is only used as a fallback
- Node.js 20+ (only needed if running frontend outside Docker)

### Step 1: Put your files in the right places

```bash
# Your downloaded PDFs
cp ~/Downloads/*.pdf knowledge-base/pdfs/

# Your official ITR JSON schema (from ITD utility download)
cp ~/Downloads/*.json knowledge-base/form_files/

# Your official ITR Excel field map (if downloaded)
cp ~/Downloads/*.xlsx knowledge-base/form_files/
```

### Step 2: Configure environment

```bash
cp .env.example .env
# Edit .env and set at least ONE of:
# GROQ_API_KEY=gsk_...        (free — console.groq.com, no card needed, tried first)
# OPENROUTER_API_KEY=sk-or-...  (free-tier models — openrouter.ai, tried second)
# OPENAI_API_KEY=sk-...         (paid last resort — only used if the two above fail)
#
# SKIP_AUTH=true     (keep this for local development)
# JWT_SECRET=...      (only matters once SKIP_AUTH=false)
```

The system tries providers in that order (`shared/llm_client.py`) and falls through to the next one on failure or a missing key — you don't need all three.

### Step 3: Build the knowledge base

```bash
cd knowledge-base
pip install -r requirements.txt

# Install Playwright browser (one-time)
playwright install chromium

# Scrape reference websites
python scraper.py

# Ingest capital gains / house property / foreign income sources
python build_itr2_kb.py

# Ingest your downloaded PDFs
python pdf_ingester.py

# Load the official form schema
python itr_form_schema_loader.py

# Embed everything into FAISS (free — uses local model, no API cost)
python embedder.py --backend huggingface
python embedder.py --backend huggingface --form-type itr2
```

After this you will have:
```
vector_store/
  AY2026-27.faiss
  AY2026-27.meta.json
  AY2026-27_ITR2.faiss
  AY2026-27_ITR2.meta.json
```

### Step 4: Start all services

```bash
# Back to project root
cd ..
docker compose up --build
```

This starts: PostgreSQL → Redis → doc-parser → rag-service → agent-orchestrator → api-gateway → frontend.
Each service waits for its dependencies to pass health checks before starting.

First run takes ~5 minutes to build all Docker images.

### Step 5: Use the app

```
http://localhost:3000/upload-itr2  → document upload
http://localhost:3000/form-itr2    → filled form viewer (after pipeline runs)
http://localhost:3000/chat         → tax Q&A chat
```

### Step 6: Run tests

```bash
# From project root (no Docker needed — pure Python)
pip install pydantic pytest
pytest tests/test_tax_engine_itr2.py -v   # 9 tax engine tests
pytest tests/ -v                    # all 6 documented files, 56 tests (router/parser tests need langgraph installed)
```

### Checking service health

```bash
curl http://localhost:3001/api/health   # all services
curl http://localhost:8001/health       # RAG service + which AY loaded
curl http://localhost:8001/indexes      # which FAISS indexes are available
```

### If the scraper gets blocked

Some pages may return 403. Use the fallback:
```bash
python manual_fallback.py --all
# Or for a specific page that was blocked:
python manual_fallback.py --from-file saved_page.html --id filing_faq
```
Last resort: open the page in Chrome, Ctrl+S to save as HTML, then run the `--from-file` option above.

---

## 8. What you need to provide

| Item | Where to get it | Where to put it |
|------|----------------|-----------------|
| Groq API key (free, recommended) | console.groq.com | `.env` file |
| OpenRouter API key (free, alternative) | openrouter.ai | `.env` file |
| OpenAI API key (paid, optional fallback) | platform.openai.com | `.env` file |
| ITR instructions PDF (AY 2026-27) | incometaxindia.gov.in downloads page | `knowledge-base/pdfs/` |
| CBDT Circular 03/2025 | incometaxindia.gov.in/communications | `knowledge-base/pdfs/` |
| Income Tax Act relevant sections | indiacode.nic.in or indiankanoon.org | `knowledge-base/pdfs/` |
| Official ITR JSON schema | ITD offline utility → extract from ZIP | `knowledge-base/form_files/` |
| Official ITR Excel field map | Same ITD utility package | `knowledge-base/form_files/` |

The websites (incometax.gov.in pages, ClearTax) are scraped automatically by `scraper.py` and `build_itr2_kb.py` — you don't download those.

---

## 9. Limitations

**What it does:**
- Parses Form 16, bank statements, AIS/26AS/TIS, capital gains statements, property/home loan certificates, insurance premiums, and foreign income disclosures automatically — PDF, Excel, or legacy .xls
- Screens each filing for eligibility (Non-Resident/RNOR is out of scope) before computing
- Fills every field — salary, house property (any number of properties), other sources, deductions, capital gains (Schedule CG), foreign income (Schedule FSI) — with source citations
- Compares old vs new regime with exact statutory math
- Validates for common errors and eligibility issues
- Answers tax questions in natural language with CBDT citations
- Exports a complete filled return as JSON

**What it does not do:**
- It does not submit the return to the income tax portal. The ITD portal does not provide a public API for programmatic filing. You export the JSON and import it into the ITD offline utility, or use it to fill the online portal manually.
- It does not handle business or professional income.
- It does not handle Non-Resident or RNOR filers. Residential status changes how every income head is sourced and taxed, not just foreign-currency line items, and this codebase doesn't model that — a filing flagged as NR/RNOR is redirected out of scope rather than silently mis-computed (`graph/router.py::is_out_of_scope`).
- The eligibility check is document-driven, not a full profile questionnaire — it doesn't currently surface agricultural income > ₹5,000, directorship, unlisted equity holdings, TDS u/s 194N, or deferred ESOP tax, all of which affect real filing eligibility.
- The non-equity LTCG bucket is always taxed at the flat 12.5% rate without checking the pre-23-Jul-2024 land/building indexation election — surfaced as a disclosure warning rather than blocked, since the flat rate is still lawful for most filers in this bucket.
- It does not give legal advice. All outputs should be reviewed before filing.

**Eligibility (AY 2026-27 rules):**
- Resident and Ordinarily Resident filers only — Non-Resident/RNOR is out of scope
- Salary income (Form 16), any number of house properties, other sources (interest, dividends)
- Capital gains: equity STCG (Sec 111A), equity LTCG (Sec 112A, with the ₹1,25,000 exemption applied), non-equity LTCG/STCG (Sec 112)
- Foreign income/assets for Resident filers, computed via Schedule FSI with foreign tax credit (Sec 90/91)

---

## 10. Interview cheat sheet

| Question | Answer |
|----------|--------|
| Why microservices? | Different scaling profiles — parser runs once per upload, RAG runs per query, agent runs per pipeline trigger. Language heterogeneity is justified: Python for ML ecosystem, Node for async I/O orchestration |
| Why Python for RAG/AI? | LangGraph, pdfplumber, FAISS, sentence-transformers — no equivalent in Node. Would lose 80% of ML tooling |
| Why Node.js for gateway? | Event-driven non-blocking I/O is the correct tool for orchestrating async calls to multiple Python microservices. Justifiable, not arbitrary |
| Why FAISS not Pinecone? | FAISS locally (zero cost, full control, fast for demo). Pinecone for production scale (managed, auto-scaling). Shows you know the tradeoff |
| How do you prevent hallucination? | RAG grounds answers in retrieved context. Confidence scoring flags fields not found in documents. Validator catches tax rule violations. The explain node is deterministic string formatting off already-computed numbers — no LLM in the loop for the numbers themselves |
| How is it AY-updatable? | Versioned FAISS namespaces (e.g. AY2024-25, AY2026-27, AY2026-27_ITR2). New AY: ingest new PDFs + re-run embedder → only RAG service redeploys. Per-AY configs live in shared/tax_engine, consumed by tax_utils_itr2.py |
| What does LangGraph add over raw prompting? | Models the pipeline as a state machine — each node has a defined contract (input state, output state). Resumable, testable in isolation, clear separation of concerns. Visualisable as a graph for viva |
| What does the eligibility router actually gate? | Only Non-Resident/RNOR status — everything else (salary, any number of house properties, other sources, capital gains, foreign income for Residents) is computed by the one pipeline. Keeps that one legal distinction out of the pipeline nodes themselves |
| Is the explain node an LLM call? | No — it's deterministic string formatting off numbers the earlier nodes already computed, so there's no hallucination risk on the figures. The LLM is used elsewhere: RAG chat, and the LLM-assisted document parsers (capital gains, property, foreign income) where layouts vary too much for regex |
| Why MMR retrieval? | Prevents 5 near-identical chunks being returned for a query. Balances relevance (similarity to query) with diversity (dissimilarity to already-selected chunks). Lambda=0.6 weights relevance higher |
| What is the cross-encoder for? | Re-ranks the 5 MMR results with a more expensive but accurate model. Bi-encoder (used for FAISS) is fast but approximate. Cross-encoder sees query+document together, much better precision |
| Why a 3-provider LLM fallback chain? | Groq and OpenRouter both expose free, OpenAI-compatible APIs; `shared/llm_client.py` tries Groq, then OpenRouter, then paid OpenAI (gpt-4o-mini) only if both fail. Keeps the whole system runnable at $0 API cost for most users while still degrading gracefully |
| How does the tax computation work? | Deterministic Python math — config-driven engine in shared/tax_engine, invoked via tax_utils_itr2.py. NOT LLM inference. Exact statutory slab rates, 4% cess, marginal relief for surcharge, 3-component HRA minimum, Sec 111A/112/112A capital gains rates, Schedule FSI foreign tax credit. Covered by unit tests across several test files (tax_engine, tax_engine_itr2, validator_itr2, capital_gains_parser) |
| What if Form 16 is scanned/image-based? | Parser returns parse_confidence < 0.5 and warns user. Fix: run `ocrmypdf scanned.pdf output.pdf` before uploading, which adds a text layer |
| How is the validator different from the form filler? | Validator is a separate LangGraph node that runs after filling, checks cross-field rules (regime consistency, house-property loss sign, capital-gains sign, income consistency), and produces structured ValidationFlag objects with severities |
| Why LLM-assisted extraction for capital gains/property/foreign income but regex for Form 16? | Form 16 has a CBDT-mandated TRACES layout that's predictable enough for regex. Broker/CAMS capital gains statements and property/foreign income documents vary far more in layout across issuers, so those parsers use an LLM extraction pass (`parsers/_llm_common.py`) instead, chunking large documents so extraction doesn't fail on long statements |
