import urllib.request
import json

url = "http://localhost:8000/pipeline/run"
payload = {
    "parsed_documents": [
        {
            "doc_type": "form16",
            "data": {
                "assessment_year": "2026-27",
                "tax_regime": "new",
                "gross_salary": {"total": 1200000},
                "tds": 45000,
                "hra_exemption": 0,
                "standard_deduction": 75000,
                "professional_tax": 0,
                "other_income": {"total": 0},
                "chapter_6A": {"total": 0}
            }
        }
    ],
    "session_id": "manual-verify-run",
    "ay": "AY2026-27"
}

try:
    print(f"Sending request to {url}...")
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), 
                                 headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as response:
        print(f"Status Code: {response.getcode()}")
        result = json.loads(response.read().decode('utf-8'))
        print(json.dumps(result, indent=2))
except Exception as e:
    print(f"Failed: {e}")
