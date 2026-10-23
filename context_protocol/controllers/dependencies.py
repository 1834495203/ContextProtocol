from typing import Annotated
from uuid import UUID
from fastapi import Depends, Request
from ..services.application import ContextApplication

def get_application(request: Request) -> ContextApplication:
    return request.app.state.store

ApplicationDep = Annotated[ContextApplication, Depends(get_application)]

def current_only(request: Request) -> None:
    if {"seq", "branch_id"} & request.query_params.keys():
        raise ValueError("历史状态与分支接口已移除；这里只能读取当前上下文")


def optional_id(value: UUID | None) -> str | None:
    return str(value) if value is not None else None
