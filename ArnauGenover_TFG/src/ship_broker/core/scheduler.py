# src/ship_broker/core/scheduler.py

import asyncio
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from .database import SessionLocal, Session as DbSession
from .email_parser import EmailParser
from .auction_background import check_vessels_for_auctions
from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

async def process_emails(db: Session):
    """Process new emails"""
    try:
        parser = EmailParser(
            settings.EMAIL_ADDRESS,
            settings.EMAIL_PASSWORD,
            db,
            settings.IMAP_SERVER
        )
        emails = parser.get_emails(days=1)
        for email_data in emails:
            parser.process_and_store_email(email_data)
    except Exception as e:
        logger.error(f"Error processing emails: {str(e)}")

async def cleanup_expired_sessions(db: Session):
    """Clean up expired sessions from the database"""
    try:
        # Delete sessions that have expired
        expired_count = db.query(DbSession).filter(
            DbSession.expires < datetime.utcnow()
        ).delete()
        db.commit()
        
        if expired_count > 0:
            logger.info(f"Cleaned up {expired_count} expired sessions")
    except Exception as e:
        logger.error(f"Error cleaning up sessions: {str(e)}")

async def start_scheduler():
    """Start background tasks"""
    # Track when we last cleaned up sessions
    last_session_cleanup = datetime.utcnow()
    session_cleanup_interval = 12 * 60 * 60  # 12 hours in seconds
    
    while True:
        try:
            db = SessionLocal()
            try:
                # Process emails
                await process_emails(db)
                
                # Check for vessels that need auctions
                await check_vessels_for_auctions(db)
                
                # Clean up expired sessions every 12 hours
                current_time = datetime.utcnow()
                seconds_since_cleanup = (current_time - last_session_cleanup).total_seconds()
                
                if seconds_since_cleanup >= session_cleanup_interval:
                    await cleanup_expired_sessions(db)
                    last_session_cleanup = current_time
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"Error in scheduler: {str(e)}")
            
        # Wait before next check
        await asyncio.sleep(settings.EMAIL_CHECK_INTERVAL)  # Usually 300 seconds (5 minutes)