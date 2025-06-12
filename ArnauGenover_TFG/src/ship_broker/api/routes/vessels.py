# src/ship_broker/api/routes/vessels.py

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional, Dict
from datetime import datetime
import logging
from sqlalchemy import or_  # Add this import

from ...core.database import Vessel, Cargo
from ...core.schemas import Vessel as VesselSchema
from ...core.vessel_tracker import tracker
from ..dependencies import get_db
from ..routes.auth import get_current_user
from ...core.database import Vessel, Cargo, User

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/vessels", response_model=List[VesselSchema])
@router.get("/vessels/", response_model=List[VesselSchema])
async def get_vessels(
    skip: int = 0,
    limit: int = 100,
    include_system: bool = True,  # New parameter
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get all vessels for the authenticated user"""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required"
            )
        
        # Include system vessels if requested
        if include_system:
            vessels = db.query(Vessel).filter(
                or_(
                    Vessel.owner_id == current_user.id,
                    Vessel.owner_id == None
                )
            ).offset(skip).limit(limit).all()
            logger.info(f"Including system vessels for user {current_user.id}")
        else:
            # Only user's vessels
            vessels = db.query(Vessel).filter(
                Vessel.owner_id == current_user.id
            ).offset(skip).limit(limit).all()
            logger.info(f"Only querying vessels for user {current_user.id}")
        
        logger.info(f"Found {len(vessels)} vessels (include_system={include_system})")
        
        # Debug log vessel details
        for v in vessels:
            logger.info(f"Vessel: {v.name}, owner_id: {v.owner_id}")
            
        return vessels
    except Exception as e:
        logger.error(f"Error getting vessels: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/my-vessels")
async def get_my_vessels(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get vessels owned by the current user"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    vessels = db.query(Vessel).filter(Vessel.owner_id == current_user.id).all()
    return vessels


# In vessels.py - update the get_matching_cargoes method

@router.get("/vessels/match/{vessel_id}/cargoes", response_model=List[Dict])
async def get_matching_cargoes(
    vessel_id: int, 
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get cargoes that match a specific vessel"""
    try:
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required"
            )
            
        # Get the vessel and verify ownership or system access
        vessel = db.query(Vessel).filter(
            and_(
                Vessel.id == vessel_id,
                or_(
                    Vessel.owner_id == current_user.id,
                    Vessel.owner_id == None
                )
            )
        ).first()
        
        if not vessel:
            raise HTTPException(status_code=404, detail="Vessel not found")
            
        # Get all active cargoes for this user and system cargoes
        cargoes = db.query(Cargo).filter(
            or_(
                Cargo.owner_id == current_user.id,
                Cargo.owner_id == None
            )
        ).all()
        
        matching_cargoes = []
        
        for cargo in cargoes:
            # Calculate match score
            score = 0.0
            reason = []
            
            # Matching logic here...
            # [Keep your existing matching logic]
            
            if score >= 0.3:  # Only include good matches
                matching_cargoes.append({
                    "cargo": {
                        "id": cargo.id,
                        "type": cargo.cargo_type,
                        "quantity": cargo.quantity,
                        "load_port": cargo.load_port,
                        "discharge_port": cargo.discharge_port,
                        "laycan_start": cargo.laycan_start,
                        "laycan_end": cargo.laycan_end,
                        "rate": cargo.rate
                    },
                    "score": score,
                    "reason": "; ".join(reason)
                })
        
        # Sort by score and return top matches
        matching_cargoes.sort(key=lambda x: x['score'], reverse=True)
        return matching_cargoes[:10]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error matching cargoes: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vessels/{vessel_id}", response_model=VesselSchema)
async def get_vessel(
    vessel_id: int, 
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Get a specific vessel"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
        
    vessel = db.query(Vessel).filter(
        Vessel.id == vessel_id,
        Vessel.owner_id == current_user.id
    ).first()
    
    if vessel is None:
        raise HTTPException(status_code=404, detail="Vessel not found")
    
    return vessel