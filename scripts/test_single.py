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
    url = "http://localhost:8000/pipeline/run"
    payload = {
        "parsed_documents": [{"doc_type": "form16", "data": {"employee_name": name, "employee_pan": "ABCDE1234F"}}],
        "session_id": f"test-session-{name.replace(' ', '-')}",
        "ay": "AY2026-27"
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), 
                                     headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=60) as response:
            res = json.loads(response.read().decode('utf-8'))
            tax_comp = res.get("itr1_form", {}).get("tax_computation", {})
            actual_refund = tax_comp.get("refund")
            actual_tax = tax_comp.get("total_tax_liability")
            print(f"Refund: {actual_refund}, Tax: {actual_tax}")
            if actual_refund == expected_refund and actual_tax == expected_tax:
                print("✅ Computation matches!")
            else:
                print("❌ Computation MISMATCH!")

        # Query RAG
        time.sleep(2)
        rag_url = "http://localhost:8001/query"
        query_payload = {"question": "How much is my refund?", "session_id": f"test-session-{name.replace(' ', '-')}"}
        req = urllib.request.Request(rag_url, data=json.dumps(query_payload).encode('utf-8'), 
                                     headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=30) as response:
            res = json.loads(response.read().decode('utf-8'))
            answer = res.get("answer", "")
            print(f"RAG Answer: {answer}")
            if str(int(expected_refund)) in answer.replace(",", ""):
                print("✅ RAG answer matches!")
            else:
                print("❌ RAG answer MISMATCH!")
    except Exception as e:
        print(f"Test failed: {e}")

# Just test Arjun
run_test("Arjun Mehta", 79600.0, 140400.0)
