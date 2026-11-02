# app/core/exceptions.py
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import (
    HTTPException, WebSocketException,
    RequestValidationError, ResponseValidationError
)
from pydantic_core._pydantic_core import ValidationError
from sqlalchemy.exc import ArgumentError, DBAPIError

from app.web.utils.logger import get_logger


logger = get_logger(__name__)


async def request_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
     
     errors = exc.errors()
     await logger.error(f"Request Validation Error: {len(errors)} invalid fields")

     return JSONResponse(
         status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
         content={
            "status_code": 422,
            "message": "Request Validation Error",
            "errors": [
                {'.'.join(map(str, e['loc'])): e['msg']}
                for e in errors
            ]
        },   
    )


async def response_exception_handler(request: Request, exc: ResponseValidationError) -> JSONResponse:
     
     await logger.error("Response Validation Error")

     return JSONResponse(
         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
         content={
            "status_code": 500,
            "message": "Response Validation Error",
            "errors": "Invalid server response"
        },   
    )


async def validate_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
     
     errors = exc.errors()
     await logger.error(f"Validation Error: {len(errors)} invalid fields")

     return JSONResponse(
         status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
         content={
            "status_code": 422,
            "message": "Validation Error",
            "errors": [
                {'.'.join(map(str, e['loc'])): e['msg']}
                for e in exc.errors()
            ]
        },   
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
     
     errors = exc.detail
     await logger.error(f"HTTP Error: {errors}")

     return JSONResponse(
         status_code=exc.status_code,
         content={
            "status_code": exc.status_code,
            "message": "HTTP Error",
            "errors": errors
        },   
    )


async def websocket_exception_handler(request: Request, exc: WebSocketException) -> JSONResponse:
     
     errors = exc.reason
     await logger.error(f"HTTP Error: {errors}")

     return JSONResponse(
         status_code=exc.code,
         content={
            "status_code": exc.code,
            "message": "WebSocket Error", 
            "errors": errors
        },   
    )


async def argument_exception_handler(request: Request, exc: ArgumentError) -> JSONResponse:
     
     errors = "Invalid database operation"
     await logger.error(f"HTTP Error: {errors}")

     return JSONResponse(
         status_code=400,
         content={
            "status_code": 400,
            "message": "Argument Error", 
            "errors": errors
        },   
    )


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
     
     await logger.error(f"API Error: {type(exc).__name__}")

     return JSONResponse(
         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
         content={
            "status_code": 500,
            "message": "API Error",
            "errors": "Internal server error"

        },   
    )

async def sqlalchemy_exception_handler(request: Request, exc: DBAPIError) -> JSONResponse:
     
     errors = "Database operation failed"
     await logger.error(f"DBAPI Error: {type(exc).__name__}")

     return JSONResponse(
         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
         content={
            "status_code": 500,
            "message": "sqlalchemy DBAPIError Error",
            "errors": errors
        },   
    )


def register_exception_handlers(app: FastAPI):

    app.exception_handler(RequestValidationError)(request_exception_handler)
    app.exception_handler(ResponseValidationError)(response_exception_handler)
    app.exception_handler(ValidationError)(validate_exception_handler)
    app.exception_handler(HTTPException)(http_exception_handler)
    app.exception_handler(WebSocketException)(websocket_exception_handler)
    app.exception_handler(ArgumentError)(argument_exception_handler)
    app.exception_handler(DBAPIError)(sqlalchemy_exception_handler)
    app.exception_handler(Exception)(global_exception_handler)
    ...
