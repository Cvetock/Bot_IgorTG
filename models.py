from sqlalchemy import (
    Column, Integer, String, Date, DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
from sqlalchemy import Time

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    tg_id = Column(Integer, primary_key=True)
    role = Column(String, default="client")  # client или master

class Master(Base):
    __tablename__ = "masters"
    id = Column(Integer, primary_key=True)
    tg_id = Column(Integer, ForeignKey("users.tg_id"), unique=True)
    name = Column(String, nullable=False)
    user = relationship("User", backref="master")

class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(Integer, primary_key=True)
    master_id = Column(Integer, ForeignKey("masters.id"))
    user_id = Column(Integer, ForeignKey("users.tg_id"))
    client_name = Column(String)
    client_phone = Column(String)
    date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)

    master = relationship("Master", backref="appointments")
    user = relationship("User", backref="appointments")

class Availability(Base):
    __tablename__ = "availabilities"
    id        = Column(Integer, primary_key=True)
    master_id = Column(Integer, ForeignKey("masters.id"), nullable=False)
    date      = Column(Date, nullable=False)
    time      = Column(Time, nullable=False)  # или String, если удобнее
    master    = relationship("Master", backref="availabilities")