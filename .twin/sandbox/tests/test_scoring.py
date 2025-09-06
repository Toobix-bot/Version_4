from src.core.agents import ScoringAgent
from src.core.models import PatchProposal

def test_scoring_composite():
    agent = ScoringAgent()
    proposal = PatchProposal(
        id="x", title="T", description="desc", diff="", rationale="r"
    )
    score = agent.score(proposal)
    assert 0 <= score.composite <= 2
