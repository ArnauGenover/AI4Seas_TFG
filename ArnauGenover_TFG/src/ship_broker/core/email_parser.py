# src/ship_broker/core/email_parser.py
import email
import imaplib
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
import logging
from sqlalchemy.orm import Session

from typing import Dict, List, Optional, Tuple, Union

from .openai_helper import OpenAIHelper
from .database import Cargo, Vessel, ProcessedEmail  # Added ProcessedEmail import

logger = logging.getLogger(__name__)

@dataclass
class VesselData:
    name: str
    dwt: Optional[float] = None
    position: Optional[str] = None
    eta: Optional[datetime] = None
    open_date: Optional[datetime] = None
    vessel_type: Optional[str] = None
    description: str = ""

@dataclass
class CargoData:
    cargo_type: str
    quantity: Optional[float] = None
    load_port: Optional[str] = None
    discharge_port: Optional[str] = None
    laycan_start: Optional[datetime] = None
    laycan_end: Optional[datetime] = None
    rate: Optional[str] = None
    description: str = ""

    def is_valid(self) -> bool:
        """Strict validation of cargo data"""
        valid_cargo_types = {
            'GRAIN', 'COAL', 'IRON ORE', 'STEEL', 'FERTILIZER', 'SOYBEANS', 
            'WHEAT', 'CORN', 'BARLEY', 'CEMENT', 'BAUXITE', 'PETCOKE',
            'CARGO OPPORTUNITY'  # Added for vessel-as-cargo cases
        }
        
        # Handle "Cargo opportunity for [Vessel Name]" format
        clean_cargo_type = self.cargo_type.upper().strip()
        if clean_cargo_type.startswith('CARGO FOR'):
            return True  # Accept these as valid by default
            
        cargo_type_valid = (
            clean_cargo_type in valid_cargo_types or
            any(valid_type in clean_cargo_type for valid_type in valid_cargo_types)
        )
        
        has_quantity = self.quantity is not None and self.quantity > 0
        has_complete_route = bool(self.load_port and self.discharge_port)
        valid_ports = self._validate_ports()
        has_suspicious_text = self._check_suspicious_text()
        
        return (
            cargo_type_valid and
            valid_ports and
            not has_suspicious_text and
            (
                (has_quantity and (self.load_port or self.discharge_port)) or
                (has_complete_route and len(clean_cargo_type) > 3)
            )
        )

    def _validate_ports(self) -> bool:
        invalid_words = {'DETAILS', 'INFO', 'WITH', 'ALL', 'ETA', 'CERTIFICATES', 'LLNA', 'AGE'}
        valid_ports = True
        
        for port in [self.load_port, self.discharge_port]:
            if port:
                port = port.strip().upper()
                if (len(port) < 2 or port in invalid_words):
                    valid_ports = False
                    break
        return valid_ports

    def _check_suspicious_text(self) -> bool:
        suspicious_patterns = [
            r'DETAILS', r'INFO', r'CERTIFICATE', r'AGE',
            r'WITH ALL', r'ARRIVING', r'NEXT', r'ETA'
        ]
        return any(re.search(pattern, self.cargo_type, re.IGNORECASE) 
                  for pattern in suspicious_patterns)

class EmailParser:
    def __init__(self, email_address: str, password: str, db: Session, imap_server: str = "imap.gmail.com"):
        self.email_address = email_address
        self.password = password
        self.imap_server = imap_server
        self.db = db
        try:
            self.ai = OpenAIHelper()  
            self.use_ai = True       
        except Exception as e:
            logger.warning(f"Failed to initialize OpenAI: {str(e)}")
            self.use_ai = False

    def connect(self) -> imaplib.IMAP4_SSL:
        logger.info(f"Connecting to {self.imap_server}")
        mail = imaplib.IMAP4_SSL(self.imap_server)
        mail.login(self.email_address, self.password)
        return mail

    def get_emails(self, days: int = 1) -> List[Dict]:
        """Fetch emails from last X days, excluding already processed ones"""
        logger.info(f"Fetching emails from last {days} days")
        mail = self.connect()
        mail.select('INBOX')
        
        date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        _, messages = mail.search(None, f'(SINCE "{date}")')
        
        emails_data = []
        processed_ids = {pe.message_id for pe in self.db.query(ProcessedEmail.message_id).all()}
        
        for num in messages[0].split():
            _, msg_data = mail.fetch(num, '(RFC822)')
            email_body = msg_data[0][1]
            email_message = email.message_from_bytes(email_body)
            
            message_id = email_message["Message-ID"] or email_message["message-id"]
            
            # Skip if already processed
            if message_id and message_id in processed_ids:
                logger.info(f"Skipping already processed email: {email_message['subject']}")
                continue
            
            # Skip if subject matches a previously processed email
            subject = email_message["subject"] or ""
            if self.db.query(ProcessedEmail).filter(
                ProcessedEmail.subject == subject
            ).first():
                logger.info(f"Skipping already processed subject: {subject}")
                continue
            
            content = self._get_email_content(email_message)
            
            logger.info(f"Found new email with subject: {subject}")
            emails_data.append({
                'subject': subject,
                'content': content,
                'message_id': message_id
            })
        
        mail.close()
        mail.logout()
        return emails_data

    
    def has_vessel_indicators(self, text: str) -> bool:
        """Check if text has strong vessel indicators"""
        indicators = [
            r'M/V\s+[A-Z]',
            r'\bVESSEL\s+(?:DETAILS?|SPECS?|NAME)\b',
            r'\bDWT\b',
            r'\bIMO\s+NO\b',
            r'\bBUILT\b',
            r'\bFLAG\b',
            r'\bCLASS\b',
            r'\bVESSEL\s+POSITION\b',
            r'\bOPEN\s+(?:AT|IN|FROM)\b',
            r'\bETA\b'
        ]
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in indicators)

    def _get_email_content(self, email_message) -> str:
        """Extract content from email message"""
        content = ""
        if email_message.is_multipart():
            for part in email_message.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        content += part.get_payload(decode=True).decode()
                    except:
                        logger.error("Failed to decode email part")
                        continue
        else:
            try:
                content = email_message.get_payload(decode=True).decode()
            except:
                logger.error("Failed to decode email")
                content = email_message.get_payload()
        return content

    def process_and_store_email(self, email_data: Union[str, Dict]) -> Tuple[List[Cargo], List[Vessel]]:
        """Process email content and store results in database"""
        cargoes = []
        vessels = []

        try:
            # Handle both string and dict input
            content = email_data['content'] if isinstance(email_data, dict) else email_data

            if self.use_ai:
                try:
                    ai_results = self.ai.extract_info(content)
                    if ai_results:
                        # Process vessels from AI
                        for vessel_data in ai_results.get('vessels', []):
                            try:
                                vessel = VesselData(**vessel_data)
                                vessels.append(vessel)
                            except Exception as e:
                                logger.error(f"Failed to process AI vessel: {str(e)}")

                        # Process cargoes from AI
                        for cargo_data in ai_results.get('cargoes', []):
                            try:
                                cargo = CargoData(**cargo_data)
                                if cargo.is_valid():
                                    cargoes.append(cargo)
                                else:
                                    logger.debug(f"Invalid cargo from AI: {cargo_data}")
                            except Exception as e:
                                logger.error(f"Failed to process AI cargo: {str(e)}")
                except Exception as e:
                    logger.error(f"AI parsing failed: {str(e)}")

            # Fallback to regex parsing if needed
            if not vessels:
                vessels.extend(self.extract_vessels(content))
            if not cargoes:
                cargoes.extend(self.extract_cargoes(content))

            # Store results in database
            self._store_results(cargoes, vessels)
            
            # Mark email as processed if we have message_id
            if isinstance(email_data, dict) and email_data.get('message_id'):
                processed = ProcessedEmail(
                    message_id=email_data['message_id'],
                    subject=email_data.get('subject', '')
                )
                self.db.add(processed)
                self.db.commit()

            return cargoes, vessels
        except Exception as e:
            logger.error(f"Error processing email: {str(e)}")
            self.db.rollback()
            return [], []



    def _store_results(self, cargoes: List[CargoData], vessels: List[VesselData], user_id: Optional[int] = None):
        """Store parsed results in database and create auctions for vessels with ETAs"""
        try:
            # Store cargoes
            for cargo in cargoes:
                db_cargo = Cargo(
                    cargo_type=cargo.cargo_type,
                    quantity=cargo.quantity,
                    load_port=cargo.load_port,
                    discharge_port=cargo.discharge_port,
                    laycan_start=cargo.laycan_start,
                    laycan_end=cargo.laycan_end,
                    rate=cargo.rate,
                    description=cargo.description,
                    owner_id=user_id  # Set owner_id
                )
                self.db.add(db_cargo)
            
            from .auction_service import AuctionService
            auction_service = AuctionService(self.db)
            
            # Store vessels and create auctions if they have ETAs
            for vessel in vessels:
                db_vessel = Vessel(
                    name=vessel.name,
                    dwt=vessel.dwt,
                    position=vessel.position,
                    eta=vessel.eta,
                    open_date=vessel.open_date,
                    vessel_type=vessel.vessel_type,
                    description=vessel.description,
                    owner_id=user_id  # Set owner_id
                )
                self.db.add(db_vessel)
                self.db.flush()  # Get ID without committing
                
                # If vessel has ETA, create auction
                if vessel.eta:
                    auction_service.create_auction_for_vessel(db_vessel.id)
                    
            self.db.commit()
        except Exception as e:
            logger.error(f"Database storage failed: {str(e)}")
            self.db.rollback()
            raise

    def process_and_store_email(self, email_data: Union[str, Dict]) -> Tuple[List[Cargo], List[Vessel]]:
        """Process email content and store results in database"""
        cargoes = []
        vessels = []
        user_id = None

        try:
            # Extract user_id if present
            if isinstance(email_data, dict) and 'user_id' in email_data:
                user_id = email_data['user_id']
                
            # Handle both string and dict input
            content = email_data['content'] if isinstance(email_data, dict) else email_data

            if self.use_ai:
                try:
                    ai_results = self.ai.extract_info(content)
                    if ai_results:
                        # Process vessels from AI
                        for vessel_data in ai_results.get('vessels', []):
                            try:
                                vessel = VesselData(**vessel_data)
                                vessels.append(vessel)
                            except Exception as e:
                                logger.error(f"Failed to process AI vessel: {str(e)}")

                        # Process cargoes from AI
                        for cargo_data in ai_results.get('cargoes', []):
                            try:
                                cargo = CargoData(**cargo_data)
                                if cargo.is_valid():
                                    cargoes.append(cargo)
                                else:
                                    logger.debug(f"Invalid cargo from AI: {cargo_data}")
                            except Exception as e:
                                logger.error(f"Failed to process AI cargo: {str(e)}")
                except Exception as e:
                    logger.error(f"AI parsing failed: {str(e)}")

            # Fallback to regex parsing if needed
            if not vessels:
                vessels.extend(self.extract_vessels(content))
            if not cargoes:
                cargoes.extend(self.extract_cargoes(content))

            # Store results in database with user_id
            self._store_results(cargoes, vessels, user_id)
            
            # Mark email as processed if we have message_id
            if isinstance(email_data, dict) and email_data.get('message_id'):
                processed = ProcessedEmail(
                    message_id=email_data['message_id'],
                    subject=email_data.get('subject', ''),
                    user_id=user_id  # Set user_id
                )
                self.db.add(processed)
                self.db.commit()

            return cargoes, vessels
        except Exception as e:
            logger.error(f"Error processing email: {str(e)}")
            self.db.rollback()
            return [], []

    

    def has_cargo_indicators(self, text: str) -> bool:
        """Check if text has strong cargo indicators"""
        indicators = [
            r'\b\d{4,6}\s*(?:MT|MTS|KMT|K|TONS?|DWT)\b',
            r'\bLAYCAN\b',
            r'\bFREIGHT\s+RATE\b',
            r'\bLOAD(?:ING)?\s+PORT\b',
            r'\bDISCH(?:ARGE)?\s+PORT\b',
            r'\bCARGO\s+READY\b',
            r'\bDWCC\b',  # Deadweight Cargo Capacity
            r'\bCBFT\b',  # Cubic Feet
            r'\bPROPOSE\s+(?:SUITABLE\s+)?C(?:AR)?GOES?\b',
            r'\bOPEN\s+(?:AT|IN|FROM|ON)\b',
            r'\bGR:\d',  # Grain capacity
            r'\bCRANES?\b'
        ]
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in indicators)

    def is_vessel_section(self, text: str) -> bool:
        """Check if text is about vessel details"""
        vessel_indicators = [
            r'\bVESSEL\s+(?:DETAILS?|SPECS?|NAME)\b',
            r'\bDWT\b',
            r'\bIMO\s+NO\b',
            r'\bBUILT\b',
            r'\bFLAG\b',
            r'\bCLASS\b',
            r'\bVESSEL\s+POSITION\b'
        ]
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in vessel_indicators)

    def is_position_update(self, text: str) -> bool:
        """Check if text is a position update"""
        position_indicators = [
            r'\b(?:CURRENT\s+)?POSITION\b',
            r'\bETA\b',
            r'\bOPEN\s+(?:AT|IN|FROM)\b',
            r'\bARRIVING\b',
            r'\bDEPARTING\b',
            r'\bEXPECTED\b'
        ]
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in position_indicators)

    def clean_rate(self, rate: str) -> Optional[str]:
        """Clean and standardize rate format"""
        if not rate:
            return None
        rate = re.sub(r'\s+', '', rate)
        if not rate.startswith('$') and not rate.startswith('USD'):
            rate = 'USD ' + rate
        return rate

    def clean_description(self, text: str) -> str:
        """Clean and format description"""
        text = re.sub(r'^.*(?:CARGO|PARCEL)\s*(?:DETAILS?|SPECS?|INFO):?\s*', '', text, flags=re.IGNORECASE)
        text = ' '.join(text.split())
        return text

    def extract_rate(self, text: str) -> Optional[str]:
        """Extract rate information from text"""
        rate_patterns = [
            r'(?:FREIGHT|RATE)\s*:?\s*(?:USD|US\$|\$)?\s*([\d,\.]+)(?:\s*(?:USD|US\$|\$|\/)?\s*(?:MT|TON|PER MT|PMT))',
            r'(?:USD|US\$|\$)\s*([\d,\.]+)(?:\s*(?:\/|\s+PER\s+)?(?:MT|TON|METRIC TON))',
            r'([\d,\.]+)\s*(?:USD|US\$|\$)(?:\s*(?:\/|\s+PER\s+)?(?:MT|TON|METRIC TON))'
        ]
        
        for pattern in rate_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                rate_value = float(match.group(1).replace(',', ''))
                return f"USD {rate_value}/MT"
        
        return None

    def extract_cargoes(self, text: str) -> List[CargoData]:
        """Extract cargo information with regex"""
        logger.info("Extracting cargo information")
        cargoes = []
        
        sections = re.split(r'\n\s*\n', text)
        
        for section in sections:
            if self.has_cargo_indicators(section):
                # Extract basic cargo information
                cargo_type_match = re.search(r'(?:CARGO|COMMODITY)\s*:?\s*([A-Z][A-Z\s]+)', section, re.IGNORECASE)
                quantity_match = re.search(r'(?:QUANTITY|QTY|AMOUNT)\s*:?\s*([\d,\.]+)\s*(?:MT|MTS|KMT|K|TONS?)', section, re.IGNORECASE)
                load_port_match = re.search(r'(?:LOAD(?:ING)?\s+PORT|FROM)\s*:?\s*([A-Z][A-Z\s,]+)', section, re.IGNORECASE)
                discharge_port_match = re.search(r'(?:DISCH(?:ARGE)?\s+PORT|TO)\s*:?\s*([A-Z][A-Z\s,]+)', section, re.IGNORECASE)
                
                # Extract rate information
                rate = self.extract_rate(section)
                
                if cargo_type_match and quantity_match:
                    cargo = CargoData(
                        cargo_type=cargo_type_match.group(1).strip(),
                        quantity=float(quantity_match.group(1).replace(',', '')),
                        load_port=load_port_match.group(1).strip() if load_port_match else None,
                        discharge_port=discharge_port_match.group(1).strip() if discharge_port_match else None,
                        rate=rate,
                        description=self.clean_description(section)
                    )
                    
                    if cargo.is_valid():
                        logger.info(f"Found valid cargo: {cargo.cargo_type} - {cargo.quantity} MT")
                        cargoes.append(cargo)
                    else:
                        logger.debug(f"Invalid cargo entry: {cargo.cargo_type}")
        
        return cargoes


    def extract_vessels(self, text: str) -> List[VesselData]:
        """Extract vessel information with regex"""
        logger.info("Extracting vessel information")
        vessels = []
        
        vessel_indicators = [
            r'VESSEL\s+DETAILS?:?',
            r'M/?V\s+',
            r'NAME\s*:',
            r'SHIP\s+DETAILS?:?'
        ]
        
        sections = re.split(r'\n\s*\n', text)
        
        # Skip vessel extraction if we see cargo indicators in the header
        cargo_headers = [
            r'PROPOSE\s+(?:SUITABLE\s+)?CGOES',
            r'PLS\s+(?:DO\s+)?NOT\s+RECIRCULATE',
            r'AVAILABLE\s+CARGOES?:',
        ]
        
        if any(re.search(pattern, sections[0], re.IGNORECASE) for pattern in cargo_headers):
            logger.info("Skipping vessel extraction due to cargo headers")
            return vessels
        
        for section in sections:
            is_vessel_section = any(
                re.search(pattern, section, re.IGNORECASE) 
                for pattern in vessel_indicators
            )
            
            name_match = re.search(r'(?:VESSEL|NAME|M/?V)\s*:?\s*([A-Z][A-Z\s]+)', section, re.IGNORECASE)
            
            if name_match and (is_vessel_section or self.has_vessel_indicators(section)):
                vessel_name = name_match.group(1).strip()
                
                # Extract basic information
                dwt_match = re.search(r'(?:DWT|DEADWEIGHT)\s*:?\s*([\d,\.]+)', section, re.IGNORECASE)
                position_match = re.search(r'(?:POSITION|PORT|LOC)\s*:?\s*([A-Z][A-Z\s]+)', section, re.IGNORECASE)
                type_match = re.search(r'(?:TYPE|VESSEL TYPE)\s*:?\s*([A-Z][A-Z\s]+)', section, re.IGNORECASE)
                eta_match = re.search(r'ETA\s*:?\s*([\d\-\.\/]+\s*(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)?(?:\s*\d{4})?)', section, re.IGNORECASE)
                open_match = re.search(r'OPEN\s*:?\s*([\d\-\.\/]+\s*(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)?(?:\s*\d{4})?)', section, re.IGNORECASE)
                
                # Extract rate information
                rate_match = re.search(r'(?:FREIGHT|RATE)\s*:?\s*(?:USD|US\$|\$)?\s*([\d,\.]+)(?:\s*(?:USD|US\$|\$|\/)?\s*(?:MT|TON|PER MT|PMT))', section, re.IGNORECASE)
                rate = None
                if rate_match:
                    rate_value = float(rate_match.group(1).replace(',', ''))
                    rate = f"USD {rate_value}/MT"
                
                if not self.is_cargo_section(section):
                    logger.info(f"Found vessel: {vessel_name}")
                    
                    description = self.clean_description(section)
                    
                    # Add rate to description if found
                    if rate:
                        description = f"Rate: {rate}. " + description
                    
                    vessel = VesselData(
                        name=vessel_name,
                        dwt=float(dwt_match.group(1).replace(',', '')) if dwt_match else None,
                        position=position_match.group(1).strip() if position_match else None,
                        vessel_type=type_match.group(1).strip() if type_match else None,
                        eta=self.parse_date(eta_match.group(1)) if eta_match else None,
                        open_date=self.parse_date(open_match.group(1)) if open_match else None,
                        description=description
                    )
                    vessels.append(vessel)
        
        return vessels
    
    def is_cargo_section(self, text: str) -> bool:
        """Check if text is about cargo"""
        cargo_indicators = [
            r'\bCARGO\b',
            r'\bLAYCAN\b',
            r'\bFREIGHT\b',
            r'\bRATE\b',
            r'\bPROPOSE\s+SUITABLE\s+CGOES\b',
            r'\bMT\b',
            r'\bQUANTITY\b',
            r'\bDWCC\b',
            r'\bCBFT\b'
        ]
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in cargo_indicators)

    def parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse various date formats"""
        try:
            if not date_str:
                return None
                
            formats = [
                '%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d',
                '%d-%b-%Y', '%d %b %Y', '%d %B %Y',
                '%d-%m-%y', '%d/%m/%y'
            ]
            
            for fmt in formats:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
            
            # If standard formats fail, try OpenAI's date parsing if available
            if self.use_ai:
                try:
                    return self.ai.standardize_date(date_str)
                except Exception as e:
                    logger.error(f"OpenAI date parsing failed: {str(e)}")
            
            return None
        except Exception as e:
            logger.error(f"Date parsing failed: {str(e)}")
            return None