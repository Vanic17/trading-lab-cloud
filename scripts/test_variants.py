import copy
import json
import unittest
from run_paper_cycle import ROOT, fork_state, run_portfolio

class VariantsTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'config-conservative-v2.json').read_text())
        self.parent = json.loads((ROOT / 'data/state.json').read_text())
        self.now = '2026-09-16T04:00:00Z'

    def test_exact_fork_and_independent_objects(self):
        state = fork_state(self.parent, 'conservative_v2', self.config, self.now)
        for key in ('cash', 'initialCash', 'positions', 'trades', 'equityHistory', 'lastRun'):
            self.assertEqual(state[key], self.parent[key])
        self.assertEqual(state['lastReport']['equity'], self.parent['lastReport']['equity'])
        state['positions'].clear()
        self.assertTrue(self.parent['positions'])

    def scenario(self):
        state = {'active': True, 'cash': 400., 'initialCash': 500., 'positions': {'ETH': {'units': 1., 'entryPrice': 100.}}, 'trades': [], 'equityHistory': []}
        market = {'ETH': {'price': 90., 'bullish': True, 'rsi': 60.}, 'BTC': {'price': 100., 'bullish': True, 'rsi': 60.}}
        return state, market

    def test_original_still_reinvests(self):
        state, market = self.scenario()
        config = json.loads((ROOT / 'config.json').read_text())
        _, report = run_portfolio('conservative', config, state, market, [], self.now)
        self.assertTrue(any(t['side'] == 'BUY' for t in report['actions']))

    def test_no_same_cycle_reinvestment_and_eight_hour_cooldown(self):
        state, market = self.scenario()
        state, report = run_portfolio('conservative_v2', self.config, state, market, [], self.now)
        self.assertEqual([t['side'] for t in report['actions']], ['SELL'])
        self.assertEqual(state['entryBlockedUntil'], '2026-09-16T12:00:00Z')
        state, report = run_portfolio('conservative_v2', self.config, state, market, [], '2026-09-16T08:00:00Z')
        self.assertEqual(report['actions'], [])
        _, report = run_portfolio('conservative_v2', self.config, state, market, [], '2026-09-16T12:00:00Z')
        self.assertTrue(any(t['side'] == 'BUY' for t in report['actions']))

    def test_cap_and_missing_btc(self):
        state, market = self.scenario()
        state['positions'] = {}
        state['cash'] = 500.
        market = {s: {'price': 100., 'bullish': True, 'rsi': 60.} for s in ('BTC','ETH','XRP','FIL','KSM','MANA')}
        result, report = run_portfolio('conservative_v2', self.config, copy.deepcopy(state), market, [], self.now)
        self.assertLessEqual(report['positionsValue'] / report['equity'], .80001)
        del market['BTC']
        _, report = run_portfolio('conservative_v2', self.config, state, market, [], self.now)
        self.assertEqual(report['actions'], [])

    def test_inherited_exposure_is_not_forcibly_liquidated(self):
        state = fork_state(self.parent, 'conservative_v2', self.config, self.now)
        market = {s: {'price': p['entryPrice'], 'bullish': True, 'rsi': 60.} for s,p in state['positions'].items()}
        market['BTC'] = {'price': 100., 'bullish': True, 'rsi': 60.}
        _, report = run_portfolio('conservative_v2', self.config, state, market, [], self.now)
        self.assertEqual(report['actions'], [])

if __name__ == '__main__':
    unittest.main()
