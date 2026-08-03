"""
Generic tax pipeline interpreter
===================================
Walks the ordered `steps` list from a per-AY/regime config file and calls
the matching primitive. This file is AY-agnostic by construction and must
never change to support a new assessment year — only new config JSON files
(and, for a genuinely new tax mechanism, a new primitive) should change.
"""
from __future__ import annotations
import json
from pathlib import Path

from . import primitives as _p

CONFIG_DIR = Path(__file__).parent / "configs"

PRIMITIVES = {
    "aggregate_gross_income": _p.aggregate_gross_income,
    "apply_deductions": _p.apply_deductions,
    "compute_taxable_income": _p.compute_taxable_income,
    "apply_slabs": _p.apply_slabs,
    "apply_rebate": _p.apply_rebate,
    "apply_surcharge": _p.apply_surcharge,
    "apply_cess": _p.apply_cess,
    "round_statutory": _p.round_statutory,
    # ITR-2-only primitives (see shared/tax_engine/primitives.py) — only ever
    # referenced by *_ITR2_*.json configs, never by ITR-1's.
    "aggregate_house_properties": _p.aggregate_house_properties,
    "compute_capital_gains": _p.compute_capital_gains,
    "apply_special_rate_capital_gains_tax": _p.apply_special_rate_capital_gains_tax,
}


def config_path(ay: str, regime: str) -> Path:
    """Builds the path load_config() reads from. Extracted as its own
    function so callers that need to know/construct a config's location
    (e.g. shared/tax_utils_itr2.py's ITR-2 namespacing) have one shared
    place to do it instead of re-deriving the '{ay}_{regime}.json'
    convention themselves. Pure refactor of load_config's prior inline
    logic — resolves to the exact same path as before for every caller.
    """
    return CONFIG_DIR / f"{ay}_{regime}.json"


def load_config(ay: str, regime: str) -> dict:
    path = config_path(ay, regime)
    if not path.exists():
        raise FileNotFoundError(f"No tax config for AY={ay} regime={regime}: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_pipeline(config: dict, inputs: dict) -> dict:
    state = dict(inputs)
    for step in config["steps"]:
        op = step["op"]
        fn = PRIMITIVES.get(op)
        if fn is None:
            raise ValueError(
                f"Unknown primitive '{op}' referenced in config "
                f"(ay={config.get('ay')}, regime={config.get('regime')})"
            )
        state = fn(state, step.get("params", {}))
    return state


def compute(ay: str, regime: str, inputs: dict) -> dict:
    config = load_config(ay, regime)
    return run_pipeline(config, inputs)
