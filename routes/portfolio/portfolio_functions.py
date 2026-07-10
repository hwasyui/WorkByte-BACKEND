import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timezone
from functions.db_manager import get_db
from functions.logger import logger
from routes.admin.admin_moderation import scan_short_and_long_text, insert_harmful_text_queue_entry
from routes.notifications.notification_functions import NotificationFunctions
from typing import List, Optional, Dict
import uuid

# harm labels reported to the freelancer when an entry gets blocked, never the matched text
_LABEL_DISPLAY_NAMES = {
    "toxic": "toxicity",
    "toxicity": "toxicity",
    "obscene": "obscenity",
    "threat": "threats",
    "insult": "insults",
    "identity_hate": "identity-based hate speech",
}

def convert_uuids_to_str(data: Dict) -> Dict:
    """Convert all UUID objects in dict to strings."""
    if not data:
        return data
    result = {}
    for key, value in data.items():
        if hasattr(value, '__class__') and 'UUID' in value.__class__.__name__:
            result[key] = str(value)
        else:
            result[key] = value
    return result


class PortfolioFunctions:
    """Handle all portfolio-related database operations."""

    @staticmethod
    def get_all_portfolios(limit: Optional[int] = None) -> List[Dict]:
        """Fetch all portfolios."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="portfolio",
                columns=["portfolio_id", "freelancer_id", "project_title", "project_description", 
                        "project_url", "completion_date", "is_auto_generated", "contract_id", "created_at", "updated_at"],
                order_by="created_at DESC",
                limit=limit,
            )
            
            logger("PORTFOLIO_FUNCTIONS", f"Fetched {len(rows)} portfolios", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("PORTFOLIO_FUNCTIONS", f"Error fetching portfolios: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_portfolio_by_id(portfolio_id: str) -> Optional[Dict]:
        """Fetch a portfolio by ID."""
        try:
            db = get_db()
            conditions = [("portfolio_id", "=", portfolio_id)]
            rows = db.fetch_data(
                table_name="portfolio",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("PORTFOLIO_FUNCTIONS", f"Portfolio {portfolio_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("PORTFOLIO_FUNCTIONS", f"Error fetching portfolio: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_portfolios_by_freelancer_id(freelancer_id: str, visible_only: bool = True) -> List[Dict]:
        """Fetch all portfolios for a freelancer. visible_only=False is for the owner
        viewing their own list (they must still see blocked/scanning entries)."""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            if visible_only:
                conditions.append(("moderation_status", "=", "visible"))
            rows = db.fetch_data(
                table_name="portfolio",
                conditions=conditions,
                order_by="created_at DESC"
            )

            logger("PORTFOLIO_FUNCTIONS", f"Fetched {len(rows)} portfolios for freelancer {freelancer_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("PORTFOLIO_FUNCTIONS", f"Error fetching portfolios: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_portfolio(freelancer_id: str, project_title: str, project_description: str,
                         project_url: Optional[str] = None, completion_date=None,
                         is_auto_generated: Optional[bool] = False, contract_id: Optional[str] = None) -> Dict:
        """Create a new portfolio."""
        try:
            db = get_db()
            portfolio_id = str(uuid.uuid4())

            portfolio_data = {
                "portfolio_id": portfolio_id,
                "freelancer_id": freelancer_id,
                "project_title": project_title,
                "project_description": project_description,
                "project_url": project_url,
                "completion_date": completion_date,
                "is_auto_generated": is_auto_generated,
                "contract_id": contract_id,
                "moderation_status": "scanning",
            }

            db.insert_data(table_name="portfolio", data=portfolio_data)

            logger("PORTFOLIO_FUNCTIONS", f"Portfolio {portfolio_id} created", level="INFO")
            return convert_uuids_to_str(portfolio_data)

        except Exception as e:
            logger("PORTFOLIO_FUNCTIONS", f"Error creating portfolio: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_portfolio(portfolio_id: str, update_data: Dict) -> Optional[Dict]:
        """Update portfolio information."""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}

            if not update_data:
                logger("PORTFOLIO_FUNCTIONS", "No data to update", level="WARNING")
                return PortfolioFunctions.get_portfolio_by_id(portfolio_id)

            conditions = [("portfolio_id", "=", portfolio_id)]
            db.update_data(table_name="portfolio", data=update_data, conditions=conditions)

            logger("PORTFOLIO_FUNCTIONS", f"Portfolio {portfolio_id} updated", level="INFO")
            return PortfolioFunctions.get_portfolio_by_id(portfolio_id)

        except Exception as e:
            logger("PORTFOLIO_FUNCTIONS", f"Error updating portfolio: {str(e)}", level="ERROR")
            raise

    @staticmethod
    async def run_portfolio_scan(portfolio_id: str, short_text: str, long_text: str, freelancer_user_id: str) -> None:
        """scanning -> scan -> visible | blocked. Mirrors ProposalFunctions.run_proposal_scan -
        content_type/content_id are this portfolio's own ('portfolio', portfolio_id), not the
        freelancer's, so a flag can be traced back to the exact entry that caused it.

        project_title carries no context - a 1-4 word field gives the ML model nothing to
        condition on, so it is keyword-only (short_text). project_description has real
        sentence context, so it goes through the ML model (long_text). See
        scan_short_and_long_text() in admin_moderation.py."""
        try:
            PortfolioFunctions.update_portfolio(portfolio_id, {"moderation_status": "scanning"})

            result = await scan_short_and_long_text(short_text, long_text)
            scan_text = " ".join(filter(None, [short_text, long_text]))

            scanned_at = datetime.now(timezone.utc)

            if result["is_flagged"]:
                PortfolioFunctions.update_portfolio(portfolio_id, {
                    "moderation_status": "blocked",
                    "scanned_at": scanned_at,
                })
                logger(
                    "PORTFOLIO_FUNCTIONS",
                    f"Portfolio {portfolio_id} blocked, labels={result.get('detected_labels')}",
                    level="WARNING",
                )
                insert_harmful_text_queue_entry(
                    "portfolio", portfolio_id, freelancer_user_id, scan_text, result
                )
                labels = [_LABEL_DISPLAY_NAMES.get(l, l) for l in result.get("detected_labels", [])]
                try:
                    await NotificationFunctions.notify(
                        recipient_user_id=freelancer_user_id,
                        notif_type="portfolio_blocked",
                        title="Portfolio Entry Needs Changes",
                        body=f"Your portfolio entry was flagged for {', '.join(labels) or 'a policy violation'}. Edit and resubmit.",
                        data={"portfolio_id": portfolio_id},
                    )
                except Exception as notif_err:
                    logger("PORTFOLIO_FUNCTIONS", f"Blocked-portfolio notification failed (non-fatal): {notif_err}", level="WARNING")
            else:
                PortfolioFunctions.update_portfolio(portfolio_id, {
                    "moderation_status": "visible",
                    "scanned_at": scanned_at,
                })

        except Exception as e:
            logger("PORTFOLIO_FUNCTIONS", f"Portfolio scan failed for {portfolio_id}: {e}", level="ERROR")

    @staticmethod
    def delete_portfolio(portfolio_id: str) -> bool:
        """Delete a portfolio."""
        try:
            db = get_db()
            conditions = [("portfolio_id", "=", portfolio_id)]
            db.delete_data(table_name="portfolio", conditions=conditions)
            
            logger("PORTFOLIO_FUNCTIONS", f"Portfolio {portfolio_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("PORTFOLIO_FUNCTIONS", f"Error deleting portfolio: {str(e)}", level="ERROR")
            raise
