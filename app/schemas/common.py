from pydantic import BaseModel


class LiveHealth(BaseModel):
    status: str = "ok"


class ReadyHealth(BaseModel):
    db: str
    redis: str
    chroma: str
