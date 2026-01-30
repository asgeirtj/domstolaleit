from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    date_from: str | None = None
    date_to: str | None = None
