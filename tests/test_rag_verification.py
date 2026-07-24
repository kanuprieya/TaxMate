"""
Test — ASV RAG self-verification step (rag-service/main.py::_verify_retrieval)
=================================================================================
Proves the confidence-scoring step actually discriminates between well-matched
and weakly-matched retrieval, rather than always returning "verified" (which
is what the old prompt-only approach effectively did — it never withheld
confidence, it just told the LLM to promise it had checked).

Run:
    pytest tests/test_rag_verification.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "rag-service"))

from main import _verify_retrieval, CONFIDENCE_THRESHOLD


def test_no_chunks_is_zero_confidence():
    result = _verify_retrieval([])
    assert result["status"] == "no_context"
    assert result["confidence"] == 0.0


def test_strong_rerank_score_is_verified():
    """A high, unambiguous cross-encoder score (clear top match) should verify."""
    chunks = [
        {"text": "a", "_score": 5.0},
        {"text": "b", "_score": -2.0},
    ]
    result = _verify_retrieval(chunks)
    assert result["status"] == "verified"
    assert result["confidence"] >= CONFIDENCE_THRESHOLD


def test_weak_rerank_score_is_low_confidence():
    """A negative top score (cross-encoder thinks it's a poor match) should NOT verify."""
    chunks = [
        {"text": "a", "_score": -3.0},
        {"text": "b", "_score": -3.2},
    ]
    result = _verify_retrieval(chunks)
    assert result["status"] == "low_confidence"
    assert result["confidence"] < CONFIDENCE_THRESHOLD


def test_ambiguous_top_two_scores_discounted():
    """Top score alone would verify, but an indistinguishable runner-up should pull it down."""
    chunks = [
        {"text": "a", "_score": 0.3},
        {"text": "b", "_score": 0.1},  # within 0.5 of top -> ambiguity discount applies
    ]
    result = _verify_retrieval(chunks)
    undiscounted = 1 / (1 + __import__("math").exp(-0.3))
    assert result["confidence"] < round(undiscounted, 3)


def test_no_rerank_scores_falls_back_conservatively():
    """Reranker unavailable (no _score field) -> distance-only estimate, capped well below 1.0."""
    chunks = [{"text": "a", "_l2": 0.2}]
    result = _verify_retrieval(chunks)
    assert result["confidence"] <= 0.6


def test_low_confidence_flag_reaches_the_answer_text():
    """The caveat must be prepended deterministically in Python, not left to the LLM to volunteer."""
    from unittest.mock import patch
    from main import _answer

    chunks = [{"text": "Some tangentially related context.", "source": "x", "section": "y"}]
    verification = {"confidence": 0.2, "status": "low_confidence", "reason": "weak match"}

    with patch("shared.llm_client.complete_with_system", return_value="A plain grounded answer."):
        answer = _answer("some question", chunks, "AY2026-27", verification=verification)

    assert answer.startswith("⚠️ Low-confidence retrieval (20%)")
    assert "A plain grounded answer." in answer


def test_verified_confidence_has_no_caveat():
    from unittest.mock import patch
    from main import _answer

    chunks = [{"text": "Directly relevant context.", "source": "x", "section": "y"}]
    verification = {"confidence": 0.9, "status": "verified", "reason": "strong match"}

    with patch("shared.llm_client.complete_with_system", return_value="A plain grounded answer."):
        answer = _answer("some question", chunks, "AY2026-27", verification=verification)

    assert answer == "A plain grounded answer."
