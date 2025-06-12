# src/ship_broker/api/routes/email_processing.py

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status, Form
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
import logging
from datetime import datetime

from ...core.database import User, ProcessedEmail
from ...core.email_parser import EmailParser
from ...config import get_settings
from ..dependencies import get_db
from ..routes.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

async def process_user_emails(
    user_id: int,
    email_address: str,
    email_password: str,
    imap_server: str,
    db: Session,
    days: int = 1
):
    """Background task to process emails for a specific user"""
    logger.info(f"Starting email processing for user_id: {user_id}")
    
    try:
        # Create email parser for the user
        parser = EmailParser(
            email_address=email_address,
            password=email_password,
            db=db,
            imap_server=imap_server,
            user_id=user_id
        )
        
        # Get emails
        emails = parser.get_emails(days=days)
        logger.info(f"Found {len(emails)} new emails for user_id: {user_id}")
        
        # Process each email
        for email_data in emails:
            try:
                cargoes, vessels = parser.process_and_store_email(email_data)
                logger.info(f"Processed email: found {len(vessels)} vessels and {len(cargoes)} cargoes")
            except Exception as e:
                logger.error(f"Error processing email: {str(e)}")
                
        logger.info(f"Completed email processing for user_id: {user_id}")
    except Exception as e:
        logger.error(f"Email processing failed for user_id {user_id}: {str(e)}")





@router.post("/process-emails/")
async def process_emails(
    request: Request,
    reprocess: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, str]:
    """Process emails from personal broker email or the company email"""
    try:
        # Determine which email to use based on user context
        if current_user and current_user.email_address and current_user.encrypted_email_password:
            # User has configured their own email - use that
            logger.info(f"Processing emails for user {current_user.username}")
            parser = EmailParser(
                email_address=current_user.email_address,
                password=current_user.get_email_password(),
                db=db,
                imap_server=current_user.imap_server,
                user_id=current_user.id  # Make sure to pass the user_id here
            )
            owner_id = current_user.id
        else:
            # Fall back to system email from settings
            settings = get_settings()
            if not settings.EMAIL_ADDRESS or not settings.EMAIL_PASSWORD:
                return {
                    "status": "error",
                    "message": "No email credentials configured. Please set up your email in Profile settings."
                }
            
            logger.info("Processing emails from system account")
            parser = EmailParser(
                email_address=settings.EMAIL_ADDRESS,
                password=settings.EMAIL_PASSWORD,
                db=db,
                imap_server=settings.IMAP_SERVER,
                user_id=current_user.id if current_user else None  # Use current user if available
            )
            owner_id = current_user.id if current_user else None  # Use current user if available
        
        # Process emails
        emails = parser.get_emails(days=1)
        logger.info(f"Found {len(emails)} emails to process")
        
        total_vessels = 0
        total_cargoes = 0
        
        for email_data in emails:
            try:
                # Add owner information
                email_data['user_id'] = owner_id
                
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
        )


@router.get("/email-stats")
async def get_email_stats(
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get email processing statistics for the authenticated user"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    # Count processed emails
    processed_count = db.query(ProcessedEmail).filter(
        ProcessedEmail.user_id == current_user.id
    ).count()
    
    # Get latest processed date
    latest_processed = db.query(ProcessedEmail).filter(
        ProcessedEmail.user_id == current_user.id
    ).order_by(ProcessedEmail.processed_at.desc()).first()
    
    # Count associated vessels and cargoes
    vessel_count = len(current_user.vessels)
    cargo_count = len(current_user.cargoes)
    
    return {
        "processed_emails": processed_count,
        "last_processed": latest_processed.processed_at if latest_processed else None,
        "vessel_count": vessel_count,
        "cargo_count": cargo_count,
        "email_configured": bool(current_user.email_address and current_user.encrypted_email_password)
    }

@router.post("/test-email-connection")
async def test_email_connection(
    email_address: str = Form(...),
    email_password: str = Form(...),
    imap_server: str = Form(...),
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Test IMAP email connection with provided credentials"""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    try:
        # Create email parser for testing
        parser = EmailParser(
            email_address=email_address,
            password=email_password,
            db=db,
            imap_server=imap_server
        )
        
        # Try to connect
        mail = parser.connect()
        mail.select('INBOX')
        
        # Try to search (just to make sure connection is working)
        _, messages = mail.search(None, 'ALL')
        
        # Close connection
        mail.close()
        mail.logout()
        
        return {
            "status": "success", 
            "message": f"Successfully connected to {imap_server} and accessed inbox."
        }
    except Exception as e:
        logger.error(f"Email connection test failed: {str(e)}")
        return {
            "status": "error",
            "message": f"Connection failed: {str(e)}"
        }