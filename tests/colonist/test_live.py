"""The bridge's live game (live.py)."""

from catanatron.colonist.live import waiting_for_earlier_discard
from catanatron.colonist.rules import new_game
from catanatron.models.enums import Action, ActionPrompt, ActionType
from catanatron.models.player import Color, Player


def test_a_later_seats_discard_waits_for_the_earlier_seat():
    """Both players must discard after a 7; colonist reported the second
    seat's discard first (seen live, the bot's own). Catanatron asks the first
    seat first, so the live game waits for it instead of stopping."""
    game = new_game([Player(Color.RED), Player(Color.BLUE)], seed=1)
    state = game.state
    first, second = state.colors
    state.is_discarding = True
    state.current_prompt = ActionPrompt.DISCARD
    state.current_player_index = 0
    state.discard_counts = [3, 4]
    later = Action(second, ActionType.DISCARD_RESOURCE, "SHEEP")
    assert waiting_for_earlier_discard(game, later)
    state.discard_counts = [3, 0]  # the second seat owes nothing: a real error
    assert not waiting_for_earlier_discard(game, later)
    state.discard_counts = [3, 4]
    state.is_discarding = False
    assert not waiting_for_earlier_discard(game, later)
    end = Action(second, ActionType.END_TURN, None)
    assert not waiting_for_earlier_discard(game, end)
