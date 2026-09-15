"""One-time exact accounting forks; never runs trading or resets an existing fork."""
import json
from datetime import datetime, timezone
from run_paper_cycle import ROOT, PORTFOLIOS, fork_state, save

def initialize():
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    states = {}
    reports = {}
    for name, config_path, state_path, report_path in PORTFOLIOS:
        config = json.loads(config_path.read_text())
        if state_path.exists():
            state = json.loads(state_path.read_text())
        else:
            state = fork_state(states[config['parentPortfolio']], name, config, now)
            save(state, state_path)
            save(state['lastReport'], report_path)
        states[name] = state
        reports[name] = state['lastReport']
    save({'updatedAt': now, 'portfolios': reports}, ROOT / 'data' / 'portfolio_summary.json')
    for name, state in states.items():
        print(name, state['lastReport']['equity'], state['cash'], list(state['positions']))

if __name__ == '__main__':
    initialize()
