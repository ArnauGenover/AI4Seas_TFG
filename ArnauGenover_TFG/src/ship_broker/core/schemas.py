# src/ship_broker/core/schemas.py

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from .database import AuctionStatus  


class UserBase(BaseModel):
    email: str
    username: str

class UserCreate(UserBase):
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class User(UserBase):
    id: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class VesselBase(BaseModel):
    name: str
    vessel_type: Optional[str] = None
    dwt: Optional[float] = None
    position: Optional[str] = None
    eta: Optional[datetime] = None
    description: Optional[str] = None
    open_date: Optional[datetime] = None

class VesselCreate(VesselBase):
    pass

class Vessel(VesselBase):
    id: int
    created_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True

class VesselSearch(BaseModel):
    name: Optional[str] = None
    vessel_type: Optional[str] = None
    position: Optional[str] = None
    min_dwt: Optional[float] = None
    max_dwt: Optional[float] = None

class CargoBase(BaseModel):
    cargo_type: str
    quantity: float
    load_port: str
    discharge_port: Optional[str] = None
    laycan_start: Optional[datetime] = None
    laycan_end: Optional[datetime] = None
    description: Optional[str] = None
    rate: Optional[str] = None

class CargoCreate(CargoBase):
    pass

class CargoResponse(CargoBase):
    id: int
    created_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True

class Cargo(CargoBase):
    id: int
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True

class CargoSearch(BaseModel):
    cargo_type: Optional[str] = None
    load_port: Optional[str] = None
    discharge_port: Optional[str] = None
    min_quantity: Optional[float] = None
    max_quantity: Optional[float] = None

class Email(BaseModel):
    sender: str
    subject: str
    body: str
    received_date: datetime

class AuctionBase(BaseModel):
    vessel_id: int
    space_mt: float
    start_price: float
    min_price: float
    daily_reduction: float
    end_date: datetime

class AuctionCreate(AuctionBase):
    pass

class AuctionUpdate(BaseModel):
    current_price: Optional[float] = None
    status: Optional[AuctionStatus] = None
    space_sold_mt: Optional[float] = None

class Auction(AuctionBase):
    id: int
    current_price: float
    space_sold_mt: float
    status: AuctionStatus
    start_date: datetime
    created_at: datetime
    last_updated: datetime
    
    class Config:
        from_attributes = True

class AuctionBidBase(BaseModel):
    auction_id: int
    bid_space_mt: float
    sale_price: float

class AuctionBid(AuctionBidBase):
    id: int
    bidder_id: int
    sold_at: datetime

    class Config:
        from_attributes = True