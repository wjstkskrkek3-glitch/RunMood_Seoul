from pydantic import BaseModel, Field
from typing import Optional, List

class UserIntent(BaseModel):
    location: str = "서울"
    distance_km: Optional[float] = None
    difficulty: Optional[str] = None
    terrain: List[str] = Field(default_factory=list)
    environment: List[str] = Field(default_factory=list)
    mood: List[str] = Field(default_factory=list)
    wants_quiet: bool = False
    wants_night_view: bool = False
    wants_facilities: bool = True

class RecommendRequest(BaseModel):
    query: str
    top_k: int = Field(default=3, ge=1, le=5)
