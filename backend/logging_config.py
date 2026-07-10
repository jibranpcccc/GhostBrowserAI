import logging
import json
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from backend.config import get_data_dir

LOGS_DIR = get_data_dir("logs")
os.makedirs(LOGS_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOGS_DIR, "app.log")

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if hasattr(record, "profile_id"):
            log_obj["profile_id"] = record.profile_id
        if hasattr(record, "event_type"):
            log_obj["event_type"] = record.event_type
            
        return json.dumps(log_obj)

def get_logger(name="AntiDetect"):
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # File handler (JSON)
        fh = logging.FileHandler(LOG_FILE)
        fh.setFormatter(JSONFormatter())
        
        # Console handler (Standard)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('[%(levelname)s] %(module)s: %(message)s'))
        
        logger.addHandler(fh)
        logger.addHandler(ch)
        
    return logger

logger = get_logger()
