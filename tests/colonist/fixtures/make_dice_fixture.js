// Builds dice_reference.json from the colonist-tracker's line-for-line port of
// colonist's DiceControllerBalanced (test/colonistDice.js in that repo):
//
//   node tests/colonist/fixtures/make_dice_fixture.js ../colonist-tracker > tests/colonist/fixtures/dice_reference.json
//
// For each game: the seed, then every roll (roller, dice) with the weights
// colonist used for that draw. Rollers alternate, as in a 1v1 game; a few
// games have 3-4 players to exercise the 7-share rule.
const path = require('path');
const { DiceControllerBalanced } = require(path.resolve(process.argv[2], 'test/colonistDice.js'));

function xorshift(seed) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0;
    return s / 4294967296;
  };
}

const games = [];
for (let g = 1; g <= 24; g++) {
  const seed = g * 7919;
  const nPlayers = g <= 20 ? 2 : 2 + (g % 3);
  const players = ['RED', 'BLUE', 'ORANGE', 'WHITE'].slice(0, nPlayers);
  let weights = null;
  const ctrl = new DiceControllerBalanced(nPlayers, { random: xorshift(seed), onWeights: (w) => { weights = w; } });
  const rolls = [];
  for (let r = 0; r < 100; r++) {
    const p = players[r % nPlayers];
    const pair = ctrl.throwDice(p);
    rolls.push([p, pair.dice1, pair.dice2, weights.map((x) => Number(x.toPrecision(12)))]);
  }
  games.push({ seed, players, rolls });
}
process.stdout.write(JSON.stringify({ generator: 'make_dice_fixture.js', games }));
