from fastapi import HTTPException, status


def not_implemented(feature: str) -> HTTPException:
    """Raised by skeleton endpoints that later work fills in."""
    return HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=f"{feature} is not implemented yet"
    )
