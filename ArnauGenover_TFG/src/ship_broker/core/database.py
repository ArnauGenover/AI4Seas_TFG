# src/ship_broker/core/database.py

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Enum, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import enum
from passlib.context import CryptContext
from ..config import settings, get_settings
from cryptography.fernet import Fernet
from typing import Optional
import uuid

engine = create_engine(settings.SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

settings_instance = get_settings()
# Create encryption key - this should be stored securely
fernet_key = Fernet(settings_instance.ENCRYPTION_KEY.encode())

class ProcessedEmail(Base):
    __tablename__ = "processed_emails"
    
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(String, index=True)  
    subject = Column(String)
    processed_at = Column(DateTime, default=datetime.utcnow)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  
    
    # This will be defined after the User class is defined
    user = None

class AuctionStatus(enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class Session(Base):
    __tablename__ = "sessions"
    
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"))
    expires = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_activity = Column(DateTime, default=datetime.utcnow)
    server_id = Column(String, nullable=True)  # Add server_id column
    
    # This will be defined after the User class is defined
    user = None

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    verification_code = Column(String, nullable=True)
    verification_code_expires = Column(DateTime, nullable=True)
    # Email settings for accessing user's mailbox
    email_address = Column(String, nullable=True)
    encrypted_email_password = Column(String, nullable=True)
    imap_server = Column(String, default="imap.gmail.com")
    smtp_server = Column(String, default="smtp.gmail.com")
    smtp_port = Column(Integer, default=465)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    vessels = relationship("Vessel", back_populates="owner")
    cargoes = relationship("Cargo", back_populates="owner")
    auctions = relationship("Auction", back_populates="owner")
    processed_emails = relationship("ProcessedEmail", back_populates="user")
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")

    # Notification settings
    notify_matches = Column(Boolean, default=True)
    notify_auctions = Column(Boolean, default=True)
    notify_bids = Column(Boolean, default=True)
    digest_frequency = Column(String, default="realtime")  # realtime, daily, weekly
    
    # Display settings
    show_ais_data = Column(Boolean, default=True)
    show_vessel_history = Column(Boolean, default=True)
    default_latitude = Column(Float, default=0)
    default_longitude = Column(Float, default=0)
    default_zoom = Column(Integer, default=3)
    date_format = Column(String, default="iso")  # iso, us, eu
    
    # API access
    api_key = Column(String, nullable=True, unique=True)

    def verify_password(self, plain_password: str) -> bool:
        return pwd_context.verify(plain_password, self.hashed_password)

    @staticmethod
    def get_password_hash(password: str) -> str:
        return pwd_context.hash(password)
    
    # Methods for email password encryption/decryption
    def set_email_password(self, password: str):
        """Encrypt and store email password"""
        if not password:
            self.encrypted_email_password = None
            return
        encrypted = fernet_key.encrypt(password.encode())
        self.encrypted_email_password = encrypted.decode()
    
    def get_email_password(self) -> Optional[str]:
        """Decrypt and return email password"""
        if not self.encrypted_email_password:
            return None
        decrypted = fernet_key.decrypt(self.encrypted_email_password.encode())
        return decrypted.decode()

class Vessel(Base):
    __tablename__ = "vessels"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    dwt = Column(Float, nullable=True)
    position = Column(String, nullable=True)
    eta = Column(DateTime, nullable=True)
    open_date = Column(DateTime, nullable=True)
    vessel_type = Column(String, nullable=True)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Add owner relationship
    owner_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("User", back_populates="vessels")
    
    # Existing relationships
    auctions = relationship("Auction", back_populates="vessel", cascade="all, delete-orphan")

class Cargo(Base):
    __tablename__ = "cargoes"
    
    id = Column(Integer, primary_key=True, index=True)
    cargo_type = Column(String)
    quantity = Column(Float, nullable=True)
    load_port = Column(String, nullable=True)
    discharge_port = Column(String, nullable=True)
    laycan_start = Column(DateTime, nullable=True)
    laycan_end = Column(DateTime, nullable=True)
    rate = Column(String, nullable=True)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Add owner relationship
    owner_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("User", back_populates="cargoes")

class Auction(Base):
    __tablename__ = "auctions"
    
    id = Column(Integer, primary_key=True, index=True)
    vessel_id = Column(Integer, ForeignKey("vessels.id"))
    start_date = Column(DateTime, default=datetime.utcnow)
    end_date = Column(DateTime)
    space_mt = Column(Float)
    space_sold_mt = Column(Float, default=0.0)  # Added this field
    start_price = Column(Float)
    current_price = Column(Float)
    min_price = Column(Float)
    daily_reduction = Column(Float)
    status = Column(Enum(AuctionStatus), default=AuctionStatus.ACTIVE)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    owner_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("User", back_populates="auctions")
    vessel = relationship("Vessel", back_populates="auctions")
    bids = relationship("AuctionBid", back_populates="auction", cascade="all, delete-orphan")

class AuctionBid(Base):
    __tablename__ = "auction_bids"
    
    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(Integer, ForeignKey("auctions.id"))
    bid_space_mt = Column(Float)
    sale_price = Column(Float)
    sold_at = Column(DateTime, default=datetime.utcnow)
    
    # Add bidder relationship
    bidder_id = Column(Integer, ForeignKey("users.id"))
    bidder = relationship("User")
    
    # Existing relationships
    auction = relationship("Auction", back_populates="bids")

# Define the relationship after both classes are defined
ProcessedEmail.user = relationship("User", back_populates="processed_emails")
Session.user = relationship("User", back_populates="sessions")

# Create tables
Base.metadata.create_all(bind=engine)