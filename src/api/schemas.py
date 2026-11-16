from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=8000)
    retrieval_limit: int = Field(default=5, ge=1, le=100)
    final_limit: int = Field(default=3, ge=1, le=100)
    lambda_value: float = Field(default=0.5, ge=0, le=1)
    use_cache: bool = True

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value):
        if not value.strip():
            raise ValueError("Query must not be blank")
        return value

    @model_validator(mode="after")
    def valid_limits(self):
        if self.final_limit > self.retrieval_limit:
            raise ValueError("final_limit must not exceed retrieval_limit")
        return self


class CitationSource(BaseModel):
    document_id: str
    filename: str
    page: int
    chunk_id: str


class Citation(BaseModel):
    citation_id: int
    sources: list[CitationSource]


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
