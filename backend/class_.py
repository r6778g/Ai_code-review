from pydantic import BaseModel

class FeedbackInput(BaseModel):
    feedback: str