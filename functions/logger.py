import logging
import logging.handlers
import traceback
import os
import sys

# Services in this set get routed to their own rotating file instead of
# app.log. They tend to run on a timer and re-log the same handful of
# entities every cycle, which would otherwise dominate app.log's rotation
# budget and push out unrelated history.
_ISOLATED_SERVICES = {"SWEEP_WORKER"}

class Logger:
    def __init__(self, log_dir=None):
        if log_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_dir = os.path.join(base_dir, "logs")

        os.makedirs(log_dir, exist_ok=True)

        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(service)s | %(route)s | %(message)s")

        self.logger = logging.getLogger("app_logger")
  
        if not self.logger.handlers:
            self.logger.setLevel(logging.INFO)

            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            console_handler.setLevel(logging.DEBUG)

            # Logger itself is set to INFO above, so DEBUG records (per-row
            # success lines, "no dirty rows" checks) never reach either
            # handler - drop to DEBUG on self.logger above to see them again.
            file_handler = logging.handlers.RotatingFileHandler(os.path.join(log_dir, "app.log"), maxBytes=1024*1024, backupCount=10)
            file_handler.setFormatter(formatter)
            file_handler.setLevel(logging.INFO)

            self.logger.addHandler(console_handler)
            self.logger.addHandler(file_handler)

        # Dedicated logger for isolated services (currently just SWEEP_WORKER).
        # propagate=False keeps its records out of app_logger's handlers
        # entirely, so it never doubles up in app.log.
        self.isolated_logger = logging.getLogger("app_logger.isolated")

        if not self.isolated_logger.handlers:
            self.isolated_logger.setLevel(logging.INFO)
            self.isolated_logger.propagate = False

            isolated_console_handler = logging.StreamHandler()
            isolated_console_handler.setFormatter(formatter)
            self.isolated_logger.addHandler(isolated_console_handler)

            isolated_file_handler = logging.handlers.RotatingFileHandler(
                os.path.join(log_dir, "sweep.log"), maxBytes=1024 * 1024, backupCount=5
            )
            isolated_file_handler.setFormatter(formatter)
            self.isolated_logger.addHandler(isolated_file_handler)

    def log(self, service, message="", route="", level="INFO", exc_info=None):
        extra = {"service": service or "", "route": route or ""}

        level = level.upper()
        target = self.isolated_logger if service in _ISOLATED_SERVICES else self.logger

        # When exc_info isn't given, only attach a traceback if we're actually inside active
        # exception handling. Avoids the spurious "NoneType: None" that logging appends when an
        # ERROR/CRITICAL line is emitted outside an except block (validation/not-found cases).
        if exc_info is None:
            exc_info = sys.exc_info()[0] is not None

        if level == "ERROR":
            target.error(message, exc_info=exc_info, extra=extra)
        elif level == "WARNING":
            target.warning(message, extra=extra)
        elif level == "DEBUG":
            target.debug(message, extra=extra)
        elif level == "CRITICAL":
            target.critical(message, exc_info=exc_info, extra=extra)
        else:
            target.info(message, extra=extra)

logger_instance = Logger()

def logger(service, message="", route="", level="INFO", exc_info=None):
    """
    exc_info: only meaningful for ERROR/CRITICAL. Defaults to None, which
    auto-attaches a traceback only when called during active exception handling
    (inside an except block); an ERROR logged elsewhere gets no bogus traceback.
    Pass exc_info=False to force-suppress (e.g. re-logging an exception a lower
    layer already dumped), or exc_info=True to force a traceback.
    """
    logger_instance.log(service, message, route, level, exc_info=exc_info)
