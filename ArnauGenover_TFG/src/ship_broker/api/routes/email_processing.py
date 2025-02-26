# src/ship_broker/api/routes/email_processing.py

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from typing import Dict
import logging
from datetime import datetime

from ...core.email_parser import EmailParser
from ...core.database import Vessel, Cargo, ProcessedEmail, User
from ...config import Settings, get_settings
from ..dependencies import get_db
from ..routes.auth import get_current_user


router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/process-emails/", include_in_schema=True)  # Added trailing slash
async def process_emails(
    reprocess: bool = False,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings)
) -> Dict[str, str]:
    """Process emails and extract vessel/cargo information"""
    try:
        parser = EmailParser(
            email_address=settings.EMAIL_ADDRESS,
            password=settings.EMAIL_PASSWORD,
            db=db,
            imap_server=settings.IMAP_SERVER
        )
        
        if reprocess:
            # Clear existing data if reprocessing
            try:
                db.query(ProcessedEmail).delete()
                db.query(Cargo).delete()
                db.query(Vessel).delete()
                db.commit()
            except Exception as e:
                logger.error(f"Error clearing database: {str(e)}")
                db.rollback()
        
        emails = parser.get_emails(days=1)
        logger.info(f"Found {len(emails)} emails to process")
        
        total_vessels = 0
        total_cargoes = 0
        
        for email_data in emails:
            try:
                logger.info(f"Processing email with subject: {email_data['subject']}")
                cargoes, vessels = parser.process_and_store_email(email_data)
                total_vessels += len(vessels)
                total_cargoes += len(cargoes)
            except Exception as e:
                logger.error(f"Error processing individual email: {str(e)}")
                continue
        
        return {
            "status": "success",
            "message": f"Processed {len(emails)} emails. Found {total_vessels} vessels and {total_cargoes} cargoes",
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error in email processing: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )@router.post("/process-emails/")
async def process_emails(
    request: Request,
    reprocess: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, str]:
    """Process emails for the current user"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="You must be logged in to process emails"
        )
    
    if not current_user.email_address or not current_user.encrypted_email_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email credentials not set. Please update your profile."
        )
    
    try:
        parser = EmailParser(
            email_address=current_user.email_address,
            password=current_user.get_email_password(),
            db=db,
            imap_server=current_user.imap_server
        )
        
        if reprocess:
            # Clear existing data for this user only
            try:
                # Find processed emails related to this user's data
                processed_emails = db.query(ProcessedEmail).filter(
                    ProcessedEmail.user_id == current_user.id
                ).all()
                
                # Delete them along with related cargoes and vessels
                for pe in processed_emails:
                    db.delete(pe)
                
                # Delete cargoes owned by user
                db.query(Cargo).filter(Cargo.owner_id == current_user.id).delete()
                
                # Delete vessels owned by user
                db.query(Vessel).filter(Vessel.owner_id == current_user.id).delete()
                
                db.commit()
            except Exception as e:
                logger.error(f"Error clearing database: {str(e)}")
                db.rollback()
        
        emails = parser.get_emails(days=1)
        logger.info(f"Found {len(emails)} emails to process for user {current_user.username}")
        
        total_vessels = 0
        total_cargoes = 0
        
        for email_data in emails:
            try:
                logger.info(f"Processing email with subject: {email_data['subject']}")
                
                # Add user_id to the email_data for owner tracking
                email_data['user_id'] = current_user.id
                
                cargoes, vessels = parser.process_and_store_email(email_data)
                total_vessels += len(vessels)
                total_cargoes += len(cargoes)
            except Exception as e:
                logger.error(f"Error processing individual email: {str(e)}")
                continue
        
        return {
            "status": "success",
            "message": f"Processed {len(emails)} emails. Found {total_vessels} vessels and {total_cargoes} cargoes",
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error in email processing: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )