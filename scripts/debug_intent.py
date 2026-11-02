import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from services.intent_router import decide_intent, score_intents

m = "I'd like to book a trial for Leo"
print("book", decide_intent(m, lang="en", session_id="x"))
print(score_intents(m, lang="en", session_id="x"))
m2 = (
    "Daughter 4yo shy eczema Kowloon Tong budget 2000 phonics "
    "sibling discount grandparents pickup parking?"
)
print("long", decide_intent(m2, lang="en", session_id="y"))
print(score_intents(m2, lang="en", session_id="y"))
