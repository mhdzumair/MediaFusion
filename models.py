from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, PickleType

from database import Base


class TamilBlasterMovie(Base):
    __tablename__ = 'tamil_blaster_movies'

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    name: str = Column(String, unique=True, nullable=False)
    poster: str = Column(String, nullable=False)
    imdb_id: str = Column(String, nullable=True)
    tamilblaster_id: str = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now(), nullable=False)
    video_qualities: dict = Column(PickleType)
    language: str = Column(String, nullable=True)
    video_type: str = Column(String, nullable=True)
