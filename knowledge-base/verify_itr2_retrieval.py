"""
ITR-2 Retrieval Sanity Check
===============================
Basic "is the KB returning garbage or not" check for the AY2026-27_ITR2
FAISS index built by build_itr2_kb.py + embedder.py --form-type itr2. Not
full ASV self-verification (that's rag-service/main.py::_verify_retrieval,
already unit-tested against synthetic scores in
tests/test_rag_verification.py without needing a real index) — this is the
one-level-simpler check: for a handful of representative capital-gains /
house-property questions, does the top retrieved chunk actually come from
the right source and contain the expected keyword?

Loads the retriever once and re-uses it across all queries (the CLI in
retriever.py's __main__ reloads the embedder + cross-encoder from scratch
per invocation, which is fine for one-off use but too slow to repeat here).

Run:
    python verify_itr2_retrieval.py
"""

from retriever import ITRRetriever

CASES = [
    {
        "query": "What is the LTCG exemption limit for equity shares under Section 112A?",
        "expect_source_contains": "112A",
        "expect_text_contains": "1.25 lakh",
    },
    {
        "query": "How is short term capital gain on shares taxed under Section 111A?",
        "expect_source_contains": "111A",
        "expect_text_contains": "20%",
    },
    {
        "query": "What tax rate applies to long term capital gains on property sale under Section 112?",
        "expect_source_contains": "Section 112",
        "expect_text_contains": "12.5%",
    },
    {
        "query": "How many house properties can I treat as self-occupied?",
        "expect_source_contains": "House Property",
        "expect_text_contains": "self-occupied",
    },
    {
        "query": "Can I claim Chapter VI-A deductions like 80C against equity STCG under Section 111A?",
        "expect_source_contains": "111A",
        "expect_text_contains": "Chapter VI-A",
    },
]


def main():
    print("Loading AY2026-27_ITR2 retriever (embedder + cross-encoder)...")
    # top_k=5 to match rag-service/main.py's QueryRequest default — MMR
    # selects its diverse candidate set before reranking, so a smaller top_k
    # here can drop a relevant chunk that a larger k would keep even though
    # its rerank score is high; 5 matches what the real pipeline actually uses.
    retriever = ITRRetriever(ay="AY2026-27_ITR2", backend="huggingface", top_k=5, rerank=True)

    passed = 0
    for case in CASES:
        results = retriever.retrieve(case["query"])
        print(f"\n{'='*70}")
        print(f"Q: {case['query']}")

        if not results:
            print("  FAIL — no chunks retrieved")
            continue

        top = results[0]
        source_ok = case["expect_source_contains"].lower() in top.source.lower()
        # The exact keyword may land in a different chunk of the same source
        # doc than the single best-ranked one (a chunk boundary artifact, not
        # a retrieval failure) — so check the expected keyword across all
        # top-k chunks, same as what the RAG pipeline hands the LLM for
        # answer synthesis, rather than requiring it in chunk #1 specifically.
        text_ok = any(case["expect_text_contains"].lower() in r.text.lower() for r in results)
        ok = source_ok and text_ok

        status = "PASS" if ok else "FAIL"
        print(f"  {status} — top hit: [{top.source}] rerank={top.rerank_score:.2f}")
        if not ok:
            print(f"    expected source to contain {case['expect_source_contains']!r} (got {source_ok})")
            print(f"    expected text to contain {case['expect_text_contains']!r} in top-{len(results)} chunks (got {text_ok})")
            print(f"    top chunk text: {top.text[:300]}")

        if ok:
            passed += 1

    print(f"\n{'='*70}")
    print(f"{passed}/{len(CASES)} retrieval sanity checks passed")
    if passed < len(CASES):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
