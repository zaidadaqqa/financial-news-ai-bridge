import logging
from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions.custom_exceptions import DatabaseError

ModelType = TypeVar("ModelType")


class BaseRepository(Generic[ModelType]):
    def __init__(self, session: AsyncSession, model_cls: type[ModelType]):
        self.session = session
        self.model_cls = model_cls

    async def get_by_id(self, id: Any) -> ModelType | None:
        try:
            result = await self.session.execute(select(self.model_cls).filter_by(id=id))
            return result.scalars().first()
        except SQLAlchemyError as e:
            logging.error(
                f"Database get_by_id failed for {self.model_cls.__name__}: {str(e)}"
            )
            raise DatabaseError(
                f"Failed to fetch {self.model_cls.__name__} by id: {id}"
            ) from e

    async def add(self, obj: ModelType) -> ModelType:
        try:
            self.session.add(obj)
            await self.session.flush()
            return obj
        except SQLAlchemyError as e:
            logging.error(
                f"Database add failed for {self.model_cls.__name__}: {str(e)}"
            )
            raise DatabaseError(f"Failed to add {self.model_cls.__name__}") from e

    async def commit(self) -> None:
        try:
            await self.session.commit()
        except SQLAlchemyError as e:
            await self.session.rollback()
            logging.error(f"Database commit failed: {str(e)}")
            raise DatabaseError("Database commit failed") from e
