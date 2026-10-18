"""Extracted numeric budgets; no Card dependency. See docs/PORTING_MAP.md."""
import json
from pathlib import Path
DEFINITIONS=json.loads(Path(__file__).with_name('numeric-contract.json').read_text())
LIMITS={k:v['const'] for k,v in DEFINITIONS['Limits']['properties'].items()}
BUDGET_FIELDS=DEFINITIONS['Budgets']['properties']
