from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: object | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ItemsResponse(BaseModel):
    items: list[dict]
