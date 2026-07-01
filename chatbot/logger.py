"""Per-turn logging for analysis: intent, NER, slot updates, and the bot's reasoning
(the decision trace from input -> output).

Writes two files per day under log_dir/:
  - chat_YYYYMMDD.jsonl : one JSON object per turn (machine-readable, for analysis)
  - chat_YYYYMMDD.log   : human-readable pretty trace
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class TurnLogger:
    def __init__(self, log_dir: str = 'logs', enabled: bool = True):
        self.enabled = enabled
        self.dir = Path(log_dir)
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def log(self, record: dict):
        if not self.enabled:
            return
        day = time.strftime('%Y%m%d')
        with open(self.dir / f'chat_{day}.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        with open(self.dir / f'chat_{day}.log', 'a', encoding='utf-8') as f:
            f.write(self._pretty(record) + '\n')

    @staticmethod
    def _pretty(r: dict) -> str:
        L = [f"[{r['timestamp']}] session={r['session_id']} turn={r['turn']}  ({r['latency_ms']}ms)"]
        rt = f"  (reply_to={r['reply_to']})" if r.get('reply_to') else ''
        L.append(f"  IN    : {r['input']!r}{rt}")
        L.append(f"  INTENT: {r['intents']}")
        L.append(f"  NER   : {r['entities']}")
        if r.get('slot_updates'):
            ups = '; '.join(f"{u['label']}: {u['old']!r}->{u['new']!r}" for u in r['slot_updates'])
            L.append(f"  SLOTS↑: {ups}")
        L.append("  THINK :")
        for step in r.get('trace', []):
            L.append(f"     - {step}")
        L.append(f"  ACTION: {r['action']}")
        out = (r['reply'] or '').replace('\n', ' / ')
        L.append(f"  OUT   : {out[:160]}")
        L.append('  ' + '-' * 64)
        return '\n'.join(L)
