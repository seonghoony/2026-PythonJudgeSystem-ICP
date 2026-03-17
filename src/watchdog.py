import os
import sys
import time
import yaml
import logging
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

sys.path.append(str(Path(__file__).parent.parent))

from src.infrastructure import database as db
from src.infrastructure.telegram import push

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def load_monitor_config():
    path = Path("config/monitor.yaml")
    if not path.exists():
        return {"refresh_interval": 60, "lectures": [], "blacklist": [], "whitelist": []}
    with open(path) as f:
        return yaml.safe_load(f)

def run_watchdog():
    logger.info("Starting PythonJudgeSystem Watchdog...")
    
    # Track the last time we sent an alert to avoid spamming
    last_alert_time = {}
    ALERT_COOLDOWN_MINUTES = 30
    STALL_THRESHOLD_MINUTES = 5

    while True:
        try:
            config = load_monitor_config()
            lectures = config.get("lectures", [])
            commited_whitelist = config.get("whitelist", [])
            raw_blacklist = config.get("blacklist", [])
            blacklist = [int(x) for x in raw_blacklist]
            whitelist = [int(x) for x in commited_whitelist] if commited_whitelist else []
            
            now = datetime.now()
            now_pd = pd.Timestamp.now()
            
            stalled_assignments = []
            
            for lecture_id in lectures:
                # Query assignments for this lecture from DB
                sql = "SELECT id, name, week_start, week_end, last_fetched_at FROM assignments WHERE lecture_id = %s"
                assigns = db.execute_query(sql, (lecture_id,), fetch=True) or []
                
                for row in assigns:
                    aid_int = row['id']
                    aname = row['name']
                    week_start = row.get('week_start')
                    week_end = row.get('week_end')
                    last_fetched = row.get('last_fetched_at')
                    
                    should_process = False
                    week_started = True
                    
                    if week_start:
                        try:
                            # Handling standard datetime.date objects from mysql
                            ws_dt = pd.to_datetime(week_start)
                            if ws_dt > now_pd:
                                week_started = False
                        except:
                            pass
                            
                    if aid_int in blacklist:
                        continue
                    if not week_started:
                        continue
                        
                    if aid_int in whitelist:
                        should_process = True
                    else:
                        if week_end:
                            try:
                                end_dt = pd.to_datetime(week_end)
                                # End of week_end day
                                end_dt = end_dt.replace(hour=23, minute=59, second=59)
                                if end_dt + pd.Timedelta(minutes=5) > now_pd:
                                    should_process = True
                            except:
                                pass
                        else:
                            should_process = True
                            
                    if should_process:
                        if not last_fetched:
                            stall_time = None
                            is_stalled = True
                        else:
                            # last_fetched is a datetime object
                            time_diff = now - last_fetched
                            is_stalled = time_diff > timedelta(minutes=STALL_THRESHOLD_MINUTES)
                            stall_time = time_diff
                            
                        if is_stalled:
                            stalled_assignments.append({
                                'id': aid_int,
                                'name': aname,
                                'last_fetched': last_fetched,
                                'stall_time': stall_time
                            })
                            
            if stalled_assignments:
                alert_msgs = []
                for sa in stalled_assignments:
                    aid = sa['id']
                    
                    # Check cooldown
                    last_alert = last_alert_time.get(aid)
                    if not last_alert or (now - last_alert) > timedelta(minutes=ALERT_COOLDOWN_MINUTES):
                        lf_str = str(sa['last_fetched']) if sa['last_fetched'] else "Never"
                        alert_msgs.append(f"- {sa['name']} ({aid})\n  Last Fetched: {lf_str}")
                        last_alert_time[aid] = now
                        
                if alert_msgs:
                    msg = "🚨 *Watchdog Alert: Monitor Stalled!*\n"
                    msg += f"The following active assignments haven't been fetched in >{STALL_THRESHOLD_MINUTES} mins:\n\n"
                    msg += "\n".join(alert_msgs)
                    logger.error(f"Sent alert:\n{msg}")
                    push(msg)
            
            logger.debug("Check completed. Sleeping for 60s...")
            time.sleep(60)
            
        except Exception as e:
            logger.error(f"Watchdog Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run_watchdog()
