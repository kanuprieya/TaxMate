"""
ITR-2 Knowledge Base Builder — Capital Gains + House Property
==================================================================
Sibling to scraper.py, scoped to the ITR-2-only sources: Section 111A/112A
(equity STCG/LTCG), Section 112 (other long-term capital gains), and
multiple house property rules (Schedule HP). Kept as its own script rather
than folded into scraper.py because half its sources need a different fetch
strategy (see below) — this way neither script's control flow has to branch
on form type mid-function.

Two fetch strategies, one per site:
  - incometax.gov.in — Drupal, server-rendered. A plain curl with
    browser-like headers gets the full article HTML (same approach as
    manual_fallback.py uses for these same pages), so no Playwright needed.
  - cleartax.in — Next.js SPA. Confirmed by inspection: the raw HTML curl
    returns has no <article>/<main> content at all, only the page shell
    plus a __NEXT_DATA__ script tag with no article body in it either — the
    content is fetched client-side post-hydration. Without a real browser
    (Playwright, not installed in this environment) curl cannot get this
    content, matching the limitation manual_fallback.py's own docstring
    already documents ("Ctrl+S save HTML manually" as the last resort).
    CLEARTAX_ARTICLES below holds that content instead, captured verbatim
    from the live pages (via a rendering fetch) on the date noted per entry
    — the moral equivalent of the Ctrl+S workaround, minus the manual step.
    Re-run against the live URLs periodically to catch rate/threshold
    changes; each entry's date is there specifically so staleness is
    visible rather than silent.

Output (matches scraper.py's Chunk schema and embedder.py's expected path
for --form-type itr2):
    rag_output/itr2/
        raw/        — one markdown file per source
        chunks/     — one chunked JSON file per source
        combined/   — all_chunks.jsonl, ready for embedder.py --form-type itr2

Run:
    python build_itr2_kb.py
    python ../knowledge-base/embedder.py --backend huggingface --form-type itr2
"""

import json
import re
import subprocess
import hashlib
from pathlib import Path
from dataclasses import dataclass, asdict

from bs4 import BeautifulSoup
import markdownify
import tiktoken

# ── Output directories ────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent / "rag_output" / "itr2"
RAW_DIR = BASE_DIR / "raw"
CHUNKS_DIR = BASE_DIR / "chunks"
COMBINED_DIR = BASE_DIR / "combined"

for d in [RAW_DIR, CHUNKS_DIR, COMBINED_DIR]:
    d.mkdir(parents=True, exist_ok=True)

enc = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(enc.encode(text))

CHUNK_SIZE    = 512   # tokens per chunk — matches scraper.py / pdf_ingester.py
CHUNK_OVERLAP = 64


# ── Sources ────────────────────────────────────────────────────────────────────

TARGETS = [
    {
        "id":            "itr2_user_manual",
        "url":           "https://www.incometax.gov.in/iec/foportal/help/how-to-file-itr2-form",
        "source":        "e-Filing Portal — ITR-2 User Manual",
        "doc_type":      "user_manual",
        "applicable_ay": "AY2026-27",
        "section":       "Filing Procedure",
        "site_type":     "incometax",
    },
    {
        "id":            "itr2_faq",
        "url":           "https://www.incometax.gov.in/iec/foportal/help/e-filing-itr2-form-faq",
        "source":        "e-Filing Portal — ITR-2 FAQs",
        "doc_type":      "faq",
        "applicable_ay": "AY2026-27",
        "section":       "FAQs",
        "site_type":     "incometax",
    },
    {
        "id":            "cleartax_stcg_111a",
        "url":           "https://cleartax.in/s/short-term-capital-gain-on-shares",
        "source":        "ClearTax — Short Term Capital Gains on Shares (Section 111A)",
        "doc_type":      "supplementary_guide",
        "applicable_ay": "AY2026-27",
        "section":       "Capital Gains — Equity STCG (Sec 111A)",
        "site_type":     "cleartax",
    },
    {
        "id":            "cleartax_ltcg_112a",
        "url":           "https://cleartax.in/s/long-term-capital-gains-on-shares",
        "source":        "ClearTax — Long Term Capital Gains on Shares (Section 112A)",
        "doc_type":      "supplementary_guide",
        "applicable_ay": "AY2026-27",
        "section":       "Capital Gains — Equity LTCG (Sec 112A)",
        "site_type":     "cleartax",
    },
    {
        "id":            "cleartax_section_112",
        "url":           "https://cleartax.in/s/section-112-calculate-income-tax-on-long-term-capital-gains",
        "source":        "ClearTax — Section 112: Tax on Long-Term Capital Gains (Non-Equity)",
        "doc_type":      "supplementary_guide",
        "applicable_ay": "AY2026-27",
        "section":       "Capital Gains — Other LTCG (Sec 112)",
        "site_type":     "cleartax",
    },
    {
        "id":            "cleartax_house_property",
        "url":           "https://cleartax.in/s/house-property",
        "source":        "ClearTax — Income from House Property Guide",
        "doc_type":      "supplementary_guide",
        "applicable_ay": "AY2026-27",
        "section":       "Schedule HP — Multiple House Properties",
        "site_type":     "cleartax",
    },
]


# ── ClearTax content, captured verbatim from the live pages (2026-08-06) ───────
# See module docstring: cleartax.in is client-rendered, so this is the
# rendering-fetch equivalent of manual_fallback.py's "Ctrl+S and save" path.

CLEARTAX_ARTICLES = {
    "cleartax_stcg_111a": """# Short-Term Capital Gains Tax on Shares (Section 111A)

## Tax Rate and Applicability

Section 111A imposes a 20% tax rate on short-term capital gains (STCG) from listed equity shares, equity-oriented mutual funds, and units of business trusts, provided Securities Transaction Tax (STT) is paid on the transaction.

## Eligible Assets

The provision applies to:
- Equity shares of listed companies traded on recognized stock exchanges with STT paid
- Equity-oriented mutual funds traded on recognized stock exchanges with STT paid
- Units of business trusts
- Equity shares and units traded on IFSC exchanges in foreign currency (even without STT)

## Key Tax Rules

No Chapter VI-A Deductions: these gains are taxed separately and no deductions under Chapter VI-A are allowed against STCG taxable under Section 111A.

Rebate Eligibility: as per Finance Act 2025, rebate under Section 87A is not available for STCG under Section 111A.

Basic Exemption Adjustment: Indian residents may adjust STCG against available basic exemption limits; non-residents cannot claim this benefit and pay 20% on full STCG.

## Loss Set-Off Rules

Short-term capital losses can be offset against both STCG and LTCG in the same year, with unused losses carried forward for up to 8 assessment years. Losses cannot offset other income heads like salary or business income.

## Calculation Example

A transaction yielding Rs. 39,000 STCG results in tax liability of Rs. 7,800 (39,000 x 20%), excluding 4% cess, before exemption adjustments.
""",
    "cleartax_ltcg_112a": """# Section 112A of Income Tax Act — Long Term Capital Gains on Shares

## Key Provisions

Section 112A addresses Long Term Capital Gains on sale of listed equity shares, equity oriented funds, and units of business trust.

Tax Rate: 12.5% on capital gains.

Exemption: Rs. 1.25 lakh on long-term capital gains from equity investments.

## Applicability Conditions

To qualify for Section 112A's beneficial rates and exemptions, these requirements must be met:
- Securities Transaction Tax (STT) paid on both purchase and sale of equity shares
- For equity-oriented mutual funds or business trusts, STT paid at time of sale
- Securities must qualify as long-term capital assets
- Securities held for more than 12 months
- No Chapter VI-A deductions claimable against such LTCG

## Calculation Steps

1. Gather all capital gain transactions from broker statements and cross-verify with AIS
2. Classify gains into: sales before 23rd July 2024 and sales on/after 23rd July 2024
3. Calculate total sales consideration separately; reduce by purchase cost
4. Add both categories' gains; subtract Rs. 1.25 lakh exemption to arrive at taxable gains
5. Apply 12.5% tax rate

## Grandfathering Clause

Protects gains accrued before 31st January 2018 from taxation. Cost of acquisition is calculated as:

Value I = Lower of Fair Market Value (31 Jan 2018) or Actual Selling Price
Value II = Higher of Value I or Actual Purchase Price

Value II becomes the Cost of Acquisition.

## Loss Set-Off Rules

- Long-term capital losses set off only against long-term capital gains
- Cannot be set off against short-term gains
- Losses carry forward for up to 8 assessment years

## Additional Considerations

The rebate under Section 87A of the Income Tax Act is not applicable to Long-Term Capital Gains (LTCG) taxed under Section 112A.

Surcharge capped at 15% on LTCG under Section 112A.
""",
    "cleartax_section_112": """# Section 112 of Income Tax Act: Long-Term Capital Gains Taxation

## Overview

Section 112 applies to long-term capital gains on assets not covered under Section 112A. The applicable tax rate is 12.5% without indexation for most assets, though immovable property offers a choice between 20% with indexation OR 12.5% without indexation.

## Covered Assets

Long-term capital assets under Section 112 include:
- Listed securities
- Zero-coupon bonds
- Unlisted securities
- Immovable property
- Other long-term capital assets

Assets under Section 112A (equity shares, equity-oriented mutual funds, business trust units with STT paid) are taxed separately at 12.5% with a Rs. 1.25 lakh exemption.

## Tax Calculation Method

When total income includes LTCG:
1. Reduce total taxable income by LTCG amount; calculate tax on remainder
2. Calculate tax on LTCG separately at specified rates
3. Add both amounts for total liability

## Key Points

- Basic exemption limits apply to individuals/HUFs but not non-residents
- Chapter VI-A deductions cannot offset LTCG
- Holding periods: 12 months for equity securities; 24 months for other assets (the 36-month period was removed for FY 2024-25)

## Loss Treatment

Long-term capital losses may only offset long-term capital gains and can be carried forward for 8 years.
""",
    "cleartax_house_property": """# Income from House Property and Taxes

## Definition and Scope

Income from House Property refers to rental income from buildings or appurtenant land (parking, gardens, courtyards). It applies to residential or commercial properties under Section 22 of the Income Tax Act, 1961, provided:

1. The property comprises a building or part thereof with attached land
2. The taxpayer is the legal or deemed owner
3. The property isn't used for the owner's business or profession

Any rental income from a property owned by a taxpayer, whether residential or commercial, will be taxed under "Income from House Property," unless it is used for their own business.

## Classification of House Properties

### Self-Occupied House Property
- Used for personal residence; GAV is nil
- Up to two vacant properties can qualify as self-occupied
- No rental income consideration

### Let Out House Property
- Rented for whole or part of the year
- Income is taxable as rental receipts

### Deemed Let Out Property
- Properties exceeding two self-occupied properties
- Treated as a let-out property even if it is left vacant
- Automatically deemed let out regardless of actual use

## Income Calculation Framework

### Step 1: Gross Annual Value (GAV)

For Self-Occupied: Zero value.

For Let-Out/Deemed Let-Out: Higher of actual rent received or expected rent.

Expected rent calculation:
- Take higher of fair rent and municipal rent
- Cannot exceed standard rent
- Compare with actual rent received; use higher amount as GAV

Example: Municipal Value = Rs 80,000; Fair Rent = Rs 90,000; Standard Rent = Rs 75,000; Actual Rent = Rs 72,000.
Expected Rent (lower of Rs 90,000 and Rs 75,000) = Rs 75,000. GAV (higher of Rs 75,000 and Rs 72,000) = Rs 75,000.

### Step 2: Deduct Municipal/Property Tax

Property taxes which the owner pays during the previous year are only to be deducted to arrive at NAV.

Conditions:
- Only taxes paid during the FY are deductible
- If tenant pays, owner cannot claim deduction
- Unpaid taxes cannot be claimed
- Deductible even if property vacant part-year

### Step 3: Net Annual Value (NAV)

NAV = GAV - Municipal Taxes Paid

### Step 4: Standard Deduction (30%)

30% of NAV is allowed as a standard deduction from the NAV under Section 24 of the Income Tax Act.
- Applied to NAV regardless of actual expenses incurred
- No additional expense deductions beyond the 30% cap

### Step 5: Home Loan Interest Deduction

Interest on home loans for construction or purchase is deductible under Section 24(b).

Deduction limits vary by scenario:
- Self-occupied property, old regime: Rs 2 lakhs
- Let-out property: No limit
- Loan taken before April 1, 1999: Rs 30,000
- Construction not completed within 5 years of loan: Rs 30,000
- Loan for repairs/renewal: Rs 30,000

Pre-construction interest is available as a deduction in five equal installments starting the year construction completes.

### Step 6: Final Calculation

Income from House Property = NAV - (30% deduction + Interest deduction)

Example: GAV Rs 5,00,000; Municipal taxes Rs 20,000; NAV Rs 4,80,000; 30% deduction Rs 1,44,000; Interest deduction Rs 1,00,000. Taxable Income = Rs 2,36,000.

## Loss from House Property

Self-occupied properties often generate losses due to nil GAV combined with interest deductions.

Set-off rules (Old Regime):
- Maximum Rs 2 lakhs loss against other income in the same year
- Excess loss carries forward 8 years
- Forward losses only offset against house property income

New Regime (Section 115BAC): loss under house property cannot be set off under any other head. No carry-forward benefit.

## Principal Repayment Deductions (Section 80C)

Maximum Rs 1.5 lakhs within the overall Section 80C cap for:
- Principal repayment on home loans
- Stamp duty and registration charges

Conditions:
- Loan must be for purchase/construction of new property
- Property cannot be sold within 5 years of possession
- Property must be fully constructed
- Not available under the new regime (Section 115BAC)

## Special Deductions for First-Time Homeowners

Section 80EE: up to Rs 50,000 for those with only one property at loan sanction date.
Section 80EEA: extended benefits for affordable housing (April 1, 2019 - March 31, 2022); requires non-eligibility for Section 80EE.

## Joint Ownership

When property is jointly owned with co-borrowers:
- Each can claim interest deduction up to Rs 2 lakhs (old regime)
- Each can claim Rs 1.5 lakhs under Section 80C for principal, stamp duty, registration
- Deductions allowed in ownership ratio
- Both must be co-owners AND co-borrowers

## Multiple Properties

For multiple let-out properties, the calculation must be made separately for each property.

Ownership limits:
- Up to 2 properties can be self-occupied
- 3rd and subsequent properties are deemed let-out
- Loss from all house properties combined is limited to Rs 2 lakhs (old regime) for offset against other income

## Special Cases

Mixed-Use Property (Business + Residence): the portion used for business is taxed under "Profits and Gains from Business or Profession," not house property income; a portion used as self-occupied residence remains house property income.

Part-Year Let Out: the expected rent or the actual rent, whichever is higher, for the whole year is considered for calculation of income from house property.

Subletting: income from subletting is chargeable under "Other Sources," not house property income, since only the original owner's income qualifies as house property income.

Deemed Ownership (Section 27): a person exercising control is deemed owner if a property is transferred to a spouse or minor child; that person is deemed owner even if legal title lies elsewhere.
""",
}


# ── Text cleaning (mirrors scraper.py) ──────────────────────────────────────────

BOILERPLATE_PATTERNS = [
    r"skip to (?:main )?content",
    r"call us.*?(?:monday to friday|all days)",
    r"1800\s+\d[\d\s]+",
    r"\+91[-\s]\d+",
    r"(?:08|09)[:\.]\d{2}\s+hrs",
    r"login\s*register",
    r"home\s*>\s*",
    r"share this page.*?twitter",
    r"was this (?:page|article) helpful.*?$",
    r"subscribe.*?newsletter",
    r"related articles.*?$",
    r"advertisement",
    r"file now.*?(?:plan|₹)",
    r"try cleartax.*?free",
    r"ca-assisted.*?filing",
    r"efile with.*?experts",
]

def clean_text(text: str) -> str:
    for pat in BOILERPLATE_PATTERNS:
        text = re.sub(pat, "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    lines = text.split("\n")
    lines = [l.strip() for l in lines if not re.match(r"^[|>\-_=#*\s]{0,5}$", l.strip())]
    return "\n".join(lines).strip()


def html_to_markdown(html: str) -> str:
    md = markdownify.markdownify(
        html,
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "nav", "footer", "header",
               "noscript", "svg", "img", "iframe", "button",
               "form", "aside"],
    )
    return clean_text(md)


# ── incometax.gov.in fetch (curl — same approach as manual_fallback.py) ────────

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def curl_fetch(url: str) -> str | None:
    cmd = [
        # No --compressed: the curl.exe Python's subprocess resolves on this
        # machine (Windows' bundled System32 copy, not Git Bash's) is linked
        # against a libcurl build without gzip/brotli support and errors out
        # (exit 2) on that flag — confirmed by testing both curl binaries
        # directly. Uncompressed transfer works identically, just slower.
        "curl", "-sL", "--max-time", "30",
        "-A", USER_AGENT,
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H", "Accept-Language: en-IN,en;q=0.9",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=40)
        html = result.stdout.decode("utf-8", errors="replace")
        if len(html) < 500:
            print(f"  ✗ Got only {len(html)} bytes — likely blocked")
            return None
        return html
    except subprocess.TimeoutExpired:
        print("  ✗ curl timed out")
        return None


def extract_incometax_content(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "noscript", "aside", "form", "button", "iframe"]):
        tag.decompose()
    main = (
        soup.find("article")
        or soup.find("div", class_=re.compile(r"node__content|field--body"))
        or soup.find("main")
        or soup
    )
    return html_to_markdown(str(main))


# ── Chunking (identical schema/algorithm to scraper.py) ────────────────────────

@dataclass
class Chunk:
    chunk_id:       str
    source:         str
    doc_type:       str
    applicable_ay:  str
    section:        str
    url:            str
    text:           str
    token_count:    int
    chunk_index:    int
    total_chunks:   int


def split_into_chunks(text: str, metadata: dict) -> list[Chunk]:
    if metadata["doc_type"] == "faq":
        logical_blocks = re.split(r"(?=\n#{1,3} |\nQ\d*[:.)]|\nQuestion\s+\d+)", text)
    else:
        logical_blocks = re.split(r"(?=\n#{1,4} )", text)
    logical_blocks = [b.strip() for b in logical_blocks if b.strip()]

    raw_chunks: list[str] = []
    for block in logical_blocks:
        tokens = enc.encode(block)
        if len(tokens) <= CHUNK_SIZE:
            raw_chunks.append(block)
        else:
            start = 0
            while start < len(tokens):
                end = min(start + CHUNK_SIZE, len(tokens))
                raw_chunks.append(enc.decode(tokens[start:end]))
                if end == len(tokens):
                    break
                start += CHUNK_SIZE - CHUNK_OVERLAP

    total = len(raw_chunks)
    chunks = []
    for i, chunk_text in enumerate(raw_chunks):
        chunk_text = chunk_text.strip()
        if not chunk_text or count_tokens(chunk_text) < 20:
            continue
        uid = hashlib.md5(f"{metadata['url']}:{i}:{chunk_text[:50]}".encode()).hexdigest()[:12]
        chunks.append(Chunk(
            chunk_id      = f"{metadata['id']}_{i:04d}_{uid}",
            source        = metadata["source"],
            doc_type      = metadata["doc_type"],
            applicable_ay = metadata["applicable_ay"],
            section       = metadata["section"],
            url           = metadata["url"],
            text          = chunk_text,
            token_count   = count_tokens(chunk_text),
            chunk_index   = i,
            total_chunks  = total,
        ))
    return chunks


# ── Main pipeline ────────────────────────────────────────────────────────────

def process_target(target: dict) -> list[Chunk]:
    print(f"\n{'='*60}")
    print(f"Processing: {target['source']}")
    print(f"URL: {target['url']}")

    if target["site_type"] == "incometax":
        html = curl_fetch(target["url"])
        if not html:
            print(f"  ✗ Failed to fetch {target['url']}")
            return []
        markdown = extract_incometax_content(html)
    else:  # cleartax — client-rendered, use captured content (see module docstring)
        markdown = clean_text(CLEARTAX_ARTICLES[target["id"]])

    if not markdown or len(markdown) < 200:
        print(f"  ✗ Content too short ({len(markdown)} chars)")
        return []

    print(f"  ✓ {len(markdown):,} characters / ~{count_tokens(markdown):,} tokens")

    raw_path = RAW_DIR / f"{target['id']}.md"
    raw_path.write_text(
        f"# {target['source']}\n\nSource: {target['url']}\nAY: {target['applicable_ay']}\n\n---\n\n{markdown}",
        encoding="utf-8",
    )
    print(f"  ✓ Raw saved → {raw_path}")

    chunks = split_into_chunks(markdown, target)
    print(f"  ✓ {len(chunks)} chunks created")

    chunks_path = CHUNKS_DIR / f"{target['id']}_chunks.json"
    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in chunks], f, ensure_ascii=False, indent=2)
    print(f"  ✓ Chunks saved → {chunks_path}")

    return chunks


def main():
    print("\n🚀 ITR-2 Knowledge Base Builder — Capital Gains + House Property")
    print(f"   Output directory: {BASE_DIR.resolve()}\n")

    all_chunks: list[Chunk] = []
    for target in TARGETS:
        try:
            all_chunks.extend(process_target(target))
        except Exception as e:
            print(f"  ✗ Error processing {target['id']}: {e}")
            import traceback; traceback.print_exc()

    combined_path = COMBINED_DIR / "all_chunks.jsonl"
    with open(combined_path, "w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")

    print(f"\n{'='*60}")
    print(f"✅ DONE — {len(all_chunks)} total chunks across {len(TARGETS)} sources")
    print(f"\nCombined JSONL: {combined_path}")
    print(f"\nNext: python embedder.py --backend huggingface --form-type itr2")

    by_source: dict[str, int] = {}
    for c in all_chunks:
        by_source[c.source] = by_source.get(c.source, 0) + 1
    print(f"\nChunk breakdown:")
    for src, count in by_source.items():
        print(f"  {count:3d} chunks ← {src}")


if __name__ == "__main__":
    main()
