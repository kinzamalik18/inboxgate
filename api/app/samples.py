import json
from dataclasses import dataclass
from pathlib import Path

SAMPLES_FILE = Path(__file__).resolve().parents[1] / "samples" / "emails.json"


@dataclass
class Sample:
    id: str
    expected_category: str
    from_name: str
    from_email: str
    subject: str
    body: str

    def email_fields(self) -> dict:
        return {"from_name": self.from_name, "from_email": self.from_email, "subject": self.subject, "body": self.body}


def load_samples(path: Path = SAMPLES_FILE) -> list[Sample]:
    return [Sample(**item) for item in json.loads(path.read_text())]
