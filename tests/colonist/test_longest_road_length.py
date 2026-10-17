from catanatron.game import Game
from catanatron.models.board import longest_acyclic_path, longest_road_length
from catanatron.models.player import Color, RandomPlayer


def test_longest_road_length_matches_path_search():
    checked = 0
    for seed in range(6):
        players = [RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)]
        game = Game(players, seed=seed)
        while game.winning_color() is None and game.state.num_turns < 400:
            game.play_tick()
            board = game.state.board
            for color in (Color.RED, Color.BLUE):
                for comp in board.connected_components[color]:
                    if not comp:
                        continue
                    want = len(longest_acyclic_path(board, comp, color))
                    assert longest_road_length(board, comp, color) == want
                    checked += 1
    assert checked > 100
