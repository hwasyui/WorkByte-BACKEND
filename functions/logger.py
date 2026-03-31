import logging
import traceback
import os

class Logger:
    def __init__(self, log_dir=None):
        if log_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_dir = os.path.join(base_dir, "logs")

        os.makedirs(log_dir, exist_ok=True)

        self.logger = logging.getLogger("app_logger")

        if not self.logger.handlers:
            self.logger.setLevel(logging.DEBUG)

            formatter =  logging.Formatter("%(asctime)s | %(levelname)s | %(service)s | %(route)s | %(message)s")
            
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)

            file_handler = logging.handlers.RotatingFileHandler(os.path.join(log_dir, "app.log"), maxBytes=1024*1024, backupCount=10)

            file_handler.setFormatter(formatter)
            
            self.logger.addHandler(console_handler)
            self.logger.addHandler(file_handler)
    
    def log(self, service, message="", route="", level="INFO"):
        extra = {"service": service or "", "route": route or ""}

        level = level.upper()

        if level == "ERROR":
            self.logger.error(message, exc_info=True, extra=extra)
        elif level == "WARNING":
            self.logger.warning(message, extra=extra)
        elif level == "DEBUG":
            self.logger.debug(message, extra=extra)
        elif level == "CRITICAL":
            self.logger.critical(message, exc_info=True, extra=extra)
        else:
            self.logger.info(message, extra=extra)

logger_instance = Logger()

def logger(service, message="", route="", level="INFO"):
    logger_instance.log(service, message, route, level)    
