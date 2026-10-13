from catanatron.colonist.loss_report import game_facts, summarize, wilson

from .test_convert import FULL_GAME


def test_wilson_interval():
    assert wilson(0, 0) == "n=0"
    assert wilson(50, 100).startswith("50.0% [40.4, 59.6]")


def test_full_game_facts_hide_the_name():
    facts = game_facts(FULL_GAME)
    assert facts is not None
    assert len(facts["opp"]) == 8 and facts["you"] != facts["them"]
    assert set(facts["bot_final"]) == {"road", "army", "vp_cards"}


def _game(opp, won, first=None):
    return {
        "opp": opp,
        "bot_won": won,
        "ended": "win",
        "you": "RED",
        "them": "BLUE",
        "first": {"HAS_ROAD": (first, 40)} if first else {},
        "bot_final": {"road": first == "RED", "army": False, "vp_cards": 0},
        "human_final": {"road": first == "BLUE", "army": True, "vp_cards": 1},
    }


def test_summary_counts_repeat_opponents_and_meetings():
    games = [
        _game("a", True, "RED"),
        _game("b", True),
        _game("a", False, "BLUE"),
        _game("a", False, "BLUE"),
        _game("a", False, "BLUE"),
    ]
    text = "\n".join(summarize(games))
    assert "distinct opponents 2" in text
    assert "met once: bot wins 100.0%" in text and "(1/1)" in text
    assert "met 4+ times: bot wins 25.0%" in text
    assert "meetings 4+: bot wins 0.0%" in text
    assert "person took longest road first, repeat meetings: 100.0%" in text
    assert [g["nth"] for g in games] == [1, 1, 2, 3, 4]
