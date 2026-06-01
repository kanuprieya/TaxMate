import re
from typing import Optional
from pydantic import BaseModel

class Form16Data(BaseModel):
    employee_name: Optional[str] = None
    employee_pan: Optional[str] = None
    assessment_year: Optional[str] = None
    gross_salary: float = 0.0
    salary_as_per_17_1: float = 0.0
    perquisites_17_2: float = 0.0
    profits_17_3: float = 0.0
    hra_10_13a: float = 0.0
    hra_received: float = 0.0
    lta_10_5: float = 0.0
    other_exempt_10: float = 0.0
    total_exempt_10: float = 0.0
    standard_deduction_16ia: float = 0.0
    professional_tax_16iii: float = 0.0
    income_under_salary: float = 0.0
    house_property_loss: float = 0.0
    other_sources_income: float = 0.0
    sec_80c_claimed: float = 0.0
    sec_80ccd_1_claimed: float = 0.0
    sec_80d_claimed: float = 0.0
    total_vi_a_claimed: float = 0.0
    taxable_income_form16: float = 0.0
    tax_payable_form16: float = 0.0
    tds_deducted_form16: float = 0.0
    raw_text_snippet: str = ""

PART_A_PATTERNS = {
    "employee_pan":        [r"PAN\s+of\s+(?:the\s+)?(?:Employee|Deductee)[:\s]+([\w\d]+)"],
    "employee_name":       [
        r"Name\s+of\s+(?:the\s+)?(?:Employee|Deductee)[\s\r\n]+([^\n]+)",
        r"Employee\s+Name[:\s]+([^\n]+)",
        r"Name\s+of\s+the\s+Employee[:\s]*([^\n]+)",
        r"NAME\s+OF\s+EMPLOYEE[:\s]*([^\n]+)",
        r"PAN\s+of\s+Employee.*?Name\s+of\s+Employee[:\s]*([^\n]+)",
        r"Name\s+([A-Z\s]+?)\s+PAN"
    ],
    "assessment_year":     [r"Assessment\s+Year[:\s]+(20\d{2}-\d{2,4}|\d{4}-\d{2,4})"],
    "period_from":         [r"Period\s+From[:\s]+([^\n]+?)\s+To"],
    "period_to":           [r"Period\s+.*?To[:\s]+([^\n]+)"],
}

PART_B_PATTERNS = {
    "salary_as_per_17_1":         [r"17\(1\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"Salary\s+as\s+per.*?17\(1\)[\s\.\:]*([\d,]+\.?\d*)"],
    "perquisites_17_2":           [r"17\(2\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"Value\s+of\s+perquisites.*?17\(2\)[\s\.\:]*([\d,]+\.?\d*)"],
    "profits_17_3":               [r"17\(3\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"Profits\s+in\s+lieu.*?17\(3\)[\s\.\:]*([\d,]+\.?\d*)"],
    "gross_salary":               [r"GROSS\s+SALARY\s*(?:\([^\)]+\))?\s*([\d,]+\.?\d*)",
                                   r"Total\s*\(a\s*\+\s*b\s*\+\s*c\s*\)\s*([\d,]+\.?\d*)",
                                   r"Gross\s+Salary[\s\.\:]*([\d,]+\.?\d*)"],
    "hra_10_13a":                 [r"10\(13A\)[^\d\n]*([\d,]{4,}\.?\d*)",
                                   r"(?:House\s+Rent|HRA).*?10\(13A\)[^\d\n]*([\d,]{4,}\.?\d*)"],
    "hra_received":               [r"Actual\s+HRA\s*=\s*Rs\.\s*([\d,]+\.?\d*)",
                                   r"House\s+Rent\s+Allowance\s*\(HRA\)\s*([\d,]+\.?\d*)"],
    "lta_10_5":                   [r"10\(5\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"(?:Leave\s+Travel|LTA).*?10\(5\)[^\d\n]*([\d,]+\.?\d*)"],
    "other_exempt_10":            [r"10\(14\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"Other.*?10\(14\)[^\d\n]*([\d,]+\.?\d*)"],
    "total_exempt_10":            [r"Total\s+Exempt\s+Allowances[^\d\n]*([\d,]+\.?\d*)",
                                   r"Total.*?Section\s+10[\s\.\:]*([\d,]+\.?\d*)"],
    "standard_deduction_16ia":    [r"16\(ia\)[\s\.\:]*([\d,]+\.?\d*)",
                                   r"Standard\s+Deduction.*?([\d,]+\.?\d*)"],
    "professional_tax_16iii":     [r"16\(iii\)[\s\.\:]*([\d,]+\.?\d*)",
                                   r"Tax\s+on\s+employment[^\d\n]*([\d,]+\.?\d*)"],
    "income_under_salary":        [r"HEAD\s+'SALARIES'[^\d\n]*([\d,]+\.?\d*)",
                                   r"Income.*?head\s+Salaries[\s\.\:]*([\d,]+\.?\d*)"],
    "house_property_loss":        [r"House\s+Property.*?Section\s+24\(b\)\s*(-?[\d,]+\.?\d*)",
                                   r"Interest\s+on\s+Housing\s+Loan\s*(-?[\d,]+\.?\d*)"],
    "other_sources_income":       [r"Other\s+Sources\s+offered\s+for\s+TDS[\s\.\:]*([\d,]+\.?\d*)",
                                   r"Income\s+under.*?Other\s+Sources[\s\.\:]*([\d,]+\.?\d*)"],
    "sec_80c_claimed":            [r"80C[^\d\n]*([\d,]{4,}\.?\d*)"],
    "sec_80ccd_1_claimed":        [r"80CCD\(1B\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"80CCD\(1\)[^\d\n]*([\d,]+\.?\d*)"],
    "sec_80d_claimed":            [r"80D[^\d\n]*([\d,]+\.?\d*)"],
    "total_vi_a_claimed":         [r"Total\s+Deductions\s+under\s+Chapter\s+VI-A[^\d\n]*([\d,]+\.?\d*)",
                                   r"Total.*?Chapter\s+VI-A[\s\.\:]*([\d,]+\.?\d*)"],
    "taxable_income_form16":      [r"TOTAL\s+INCOME\s*\(5\s*\-\s*6\s*\)[^\d\n]*([\d,]+\.?\d*)",
                                   r"Total\s+Income[\s\.\:]*([\d,]+\.?\d*)"],
    "tax_payable_form16":         [r"TOTAL\s+TAX\s+AND\s+INTEREST\s+PAYABLE[^\d\n]*([\d,]+\.?\d*)",
                                   r"Net\s+Tax\s+Payable[^\d\n]*([\d,]+\.?\d*)"],
    "tds_deducted_form16":        [r"TDS\s+for\s+the\s+current\s+employer[^\d\n]*([\d,]+\.?\d*)",
                                   r"Total\s+Tax\s+Paid[^\d\n]*([\d,]+\.?\d*)",
                                   r"Total.*?TDS\s+deducted[\s\.\:]*([\d,]+\.?\d*)"],
}

def parse_form16_text(full_text: str) -> Form16Data:
    result = Form16Data()
    result.raw_text_snippet = full_text
    for field, patterns in {**PART_A_PATTERNS, **PART_B_PATTERNS}.items():
        for pattern in patterns:
            match = re.search(pattern, full_text, re.IGNORECASE | re.DOTALL)
            if match:
                val_str = match.group(1).replace(",", "")
                try:
                    if field in ["employee_name", "employee_pan", "assessment_year"]:
                        setattr(result, field, match.group(1).strip())
                    else:
                        setattr(result, field, float(val_str))
                    break
                except:
                    continue
    return result

def parse_form16(path: str) -> Form16Data:
    import pdfplumber
    full_text = ""
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"
    return parse_form16_text(full_text)

def form16_to_dict(data: Form16Data) -> dict:
    regime = "new"
    text = data.raw_text_snippet.lower()
    if any(k in text for k in ["old tax regime", "10-iea", "10-ie", "opted out of 115bac"]):
        regime = "old"
    elif any(k in text for k in ["new tax regime", "115bac(1a)", "default regime"]):
        regime = "new"
    
    return {
        "employee_name": data.employee_name,
        "employee_pan": data.employee_pan,
        "assessment_year": data.assessment_year or "2026-27",
        "tax_regime": regime,
        "tds": data.tds_deducted_form16,
        "gross_salary": {
            "salary_17_1":      data.salary_as_per_17_1,
            "perquisites_17_2":  data.perquisites_17_2,
            "profits_17_3":      data.profits_17_3,
            "total":            data.gross_salary or (data.salary_as_per_17_1 + data.perquisites_17_2 + data.profits_17_3)
        },
        "total_exemptions": data.total_exempt_10 or (data.hra_10_13a + data.lta_10_5 + data.other_exempt_10),
        "hra_exempt": data.hra_10_13a,
        "hra_received": data.hra_received,
        "standard_deduction": data.standard_deduction_16ia or (75000.0 if regime == "new" else 50000.0),
        "professional_tax": data.professional_tax_16iii,
        "other_income": {
            "house_property": data.house_property_loss,
            "other_sources": data.other_sources_income,
            "total": data.house_property_loss + data.other_sources_income
        },
        "chapter_6A": {
            "80C": data.sec_80c_claimed,
            "80D": data.sec_80d_claimed,
            "total": data.total_vi_a_claimed or (data.sec_80c_claimed + data.sec_80d_claimed)
        }
    }
