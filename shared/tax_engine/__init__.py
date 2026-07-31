from .interpreter import compute, load_config, run_pipeline, PRIMITIVES, config_path
from .primitives import round_to_nearest_10

ITR2_SUFFIX = "_ITR2"


def itr2_ay(ay: str) -> str:
    """Namespaces an AY string for ITR-2 configs (configs/{ay}_ITR2_{regime}.json
    — see shared/tax_utils_itr2.py::compute_tax_from_engine_itr2). Lives here
    rather than in interpreter.py, which stays free of any 'ITR-2' concept by
    design (see its module docstring) — this is just the package's public
    surface, already home to cross-cutting exports like round_to_nearest_10.
    Centralizing it means every caller that needs this convention uses the
    identical suffix instead of each hand-rolling f"{ay}_ITR2" separately.
    """
    return ay if ay.endswith(ITR2_SUFFIX) else f"{ay}{ITR2_SUFFIX}"


__all__ = [
    "compute", "load_config", "run_pipeline", "PRIMITIVES", "round_to_nearest_10",
    "config_path", "itr2_ay",
]
