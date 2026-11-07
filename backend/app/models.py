import enum
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text, Index, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

def now_utc():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(140))
    role: Mapped[str] = mapped_column(String(16), default='client')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

class Movie(Base):
    __tablename__ = 'movies'
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    duration_min: Mapped[int] = mapped_column(Integer)
    genre: Mapped[str] = mapped_column(String(100))
    poster_url: Mapped[str] = mapped_column(Text, default='')
    rating: Mapped[str] = mapped_column(String(10), default='12+')
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Hall(Base):
    __tablename__ = 'halls'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    rows: Mapped[int] = mapped_column(Integer)
    seats_per_row: Mapped[int] = mapped_column(Integer)

class Screening(Base):
    __tablename__ = 'screenings'
    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey('movies.id'))
    hall_id: Mapped[int] = mapped_column(ForeignKey('halls.id'))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price_cents: Mapped[int] = mapped_column(Integer, default=65000)
    movie: Mapped[Movie] = relationship()
    hall: Mapped[Hall] = relationship()

class Booking(Base):
    __tablename__ = 'bookings'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    screening_id: Mapped[int] = mapped_column(ForeignKey('screenings.id'))
    seat_code: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(20), default='confirmed')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (Index('uq_active_screening_seat', 'screening_id','seat_code',unique=True,postgresql_where=text("status = 'confirmed'")),)

class Category(Base):
    __tablename__ = 'categories'
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(80))

class NotificationTemplate(Base):
    __tablename__ = 'notification_templates'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    category_slug: Mapped[str] = mapped_column(String(50), default='general')

class Notification(Base):
    __tablename__ = 'notifications'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    category_slug: Mapped[str] = mapped_column(String(50))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_status: Mapped[str] = mapped_column(String(30), default='pending')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
