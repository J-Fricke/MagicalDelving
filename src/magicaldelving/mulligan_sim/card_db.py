import json
from importlib import resources
from typing import Any, Dict


def load_card_db() -> Dict[str, Any]:
    # expects card_db.json to be packaged alongside this module
    with resources.files(__package__).joinpath("card_db.json").open("r", encoding="utf-8") as f:
        return json.load(f)