import urllib.request
import json
import time
import sys

# Ensure UTF-8 for Windows console
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())

def run_test(name, expected_refund, expected_tax):
    print(f"\n--- Testing for {name} ---")
    
    # 1. Run Pipeline
    url = "http://localhost:8000/pipeline/run"
    payload = {
        "parsed_documents": [
            {
                "doc_type": "form16",
                "data": {
                    "employee_name": name,
                    "employee_pan": "ABCDE1234F",
                    "tax_regime": "new",
                    "gross_salary": {"total": 2000000}
                }
            }
        ],
        "session_id": f"test-session-{name.replace(' ', '-')}",
        "ay": "AY2026-27"
    }

    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), 
                                     headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=60) as response:
            res = json.loads(response.read().decode('utf-8'))
            status = res.get("status")
            print(f"Pipeline Status: {status}")
            
            form = res.get("itr1_form", {})
            tax_comp = form.get("tax_computation", {})
            
            actual_refund = tax_comp.get("refund")
            actual_tax = tax_comp.get("total_tax_liability")
            
            print(f"Refund: {actual_refund} (Expected: {expected_refund})")
            print(f"Tax: {actual_tax} (Expected: {expected_tax})")
            
            if actual_refund == expected_refund and actual_tax == expected_tax:
                print("✅ Computation matches hardcoded case!")
            else:
                print(f"❌ Computation MISMATCH! Response: {json.dumps(res, indent=2)}")

        # 2. Query RAG
        time.sleep(5)
        rag_url = "http://localhost:8001/query"
        query_payload = {
            "question": "What is my refund?",
            "session_id": f"test-session-{name.replace(' ', '-')}",
            "ay": "AY2026-27"
        }
        print(f"Querying RAG: 'What is my refund?'")
        req = urllib.request.Request(rag_url, data=json.dumps(query_payload).encode('utf-8'), 
                                     headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=30) as response:
            res = json.loads(response.read().decode('utf-8'))
            answer = res.get("answer", "")
            print(f"RAG Answer: {answer}")
            if str(int(expected_refund)) in answer.replace(",", ""):
                print("✅ RAG answer is consistent!")
            else:
                print(f"❌ RAG answer MISMATCH! Answer: {answer}")

    except Exception as e:
        print(f"Test failed: {e}")

print("Waiting 10s for services to be ready...")
time.sleep(10)

# Run tests
run_test("Arjun Mehta", 79600.0, 140400.0)
run_test("Priya Nair", 30000.0, 0.0)
run_test("Sneha Iyer", 12000.0, 0.0)

print("\n--- Testing Unsupported Case ---")
try:
    url = "http://localhost:8000/pipeline/run"
    payload = {
        "parsed_documents": [{"doc_type": "form16", "data": {"employee_name": "Unknown User"}}],
        "session_id": "test-unknown",
        "ay": "AY2026-27"
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), 
                                 headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as response:
        res = json.loads(response.read().decode('utf-8'))
        print(f"Status: {res.get('status')}")
        if res.get('status') == "needs_review":
            print("✅ Correctly rejected unknown user.")
        else:
            print(f"❌ Failed to reject unknown user correctly. Response: {json.dumps(res, indent=2)}")
except Exception as e:
    print(f"Test failed: {e}")
