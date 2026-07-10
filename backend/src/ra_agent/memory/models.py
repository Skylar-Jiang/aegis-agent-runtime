from pydantic import BaseModel


class MemoryRecord(BaseModel):
    memory_id: str
    content: str
    trusted: bool = False
