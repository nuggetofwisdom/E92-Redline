from dataclasses import dataclass
from pathlib import Path
import yaml

@dataclass(frozen=True)
class PidDef:
    id: str
    name: str
    pid: int
    bytes: int
    formula: str
    unit: str
    hz: float

    def decode(self, data: bytes) -> float:
        names = dict(zip("ABCD", data))
        # Formulas come from your own YAML; builtins are stripped as a basic guard.
        return float(eval(self.formula, {"__builtins__": {}}, names))

def load_pids(path: Path | None = None) -> list[PidDef]:
    path = path or Path(__file__).with_name("pids.yaml")
    with open(path) as f:
        return [PidDef(**p) for p in yaml.safe_load(f)["pids"]]
