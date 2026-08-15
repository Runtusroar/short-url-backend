import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings


def main() -> None:
    print(json.dumps(settings.public_summary(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
