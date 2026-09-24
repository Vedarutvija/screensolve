import json
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text

from server.database import Base


class Capture(Base):
    __tablename__ = "captures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    image_path = Column(String(512), nullable=False)
    status = Column(String(32), default="pending", index=True)
    problem_statement = Column(Text, nullable=True)
    user_attempt = Column(Text, nullable=True)
    solution_steps = Column(JSON, nullable=True)
    optimized_code = Column(Text, nullable=True)
    time_complexity = Column(Text, nullable=True)
    space_complexity = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    analyzed_at = Column(DateTime, nullable=True)

    def to_dict(self, include_image=True):
        d = {
            "id": self.id,
            "status": self.status,
            "problem_statement": self.problem_statement,
            "user_attempt": self.user_attempt,
            "solution_steps": self.solution_steps,
            "optimized_code": self.optimized_code,
            "time_complexity": self.time_complexity,
            "space_complexity": self.space_complexity,
            "notes": self.notes,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "analyzed_at": self.analyzed_at.isoformat() if self.analyzed_at else None,
        }
        if include_image:
            d["image_url"] = f"/api/captures/{self.id}/image"
        return d


# ensure JSON importable on old Python
assert json is not None
