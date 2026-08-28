"""
Learning package — mismatch feedback and learned-rule retrieval.

Components:
  retrieval.py  — Query rule_book_learned.json for relevant past corrections
  feedback.py   — Record mismatch decisions and persist corrections as learned rules
"""

from learning.retrieval import LearnedRuleRetriever
from learning.feedback import FeedbackRecorder, MismatchFeedback

__all__ = [
    "LearnedRuleRetriever",
    "FeedbackRecorder",
    "MismatchFeedback",
]
