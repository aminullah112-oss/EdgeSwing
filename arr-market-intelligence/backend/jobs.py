"""Throttled full-universe daily candle refresh and EOD report scheduler."""
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from backend.db import universe, create_job, update_job, latest_job, save_report
from backend.kite_service import KiteService
from backend.analysis import daily_report
from backend.signals import record_and_update

class EODScheduler:
    def __init__(self): self.running=False; self._stop=threading.Event(); self._thread=None; self._last_run=None
    def start(self):
        if self.running:return
        self.running=True; self._stop.clear()
        self._thread=threading.Thread(target=self._loop,daemon=True,name='arr-eod-scheduler'); self._thread.start()
    def _loop(self):
        tz=ZoneInfo('Asia/Kolkata')
        while not self._stop.wait(20):
            now=datetime.now(tz); key=now.date().isoformat()
            if now.weekday()<5 and (now.hour,now.minute)>=(15,50) and self._last_run!=key:
                self._last_run=key
                try:_scheduled_run()
                except Exception:pass
    def shutdown(self,wait=False):
        self._stop.set(); self.running=False
        if wait and self._thread:self._thread.join(timeout=2)

scheduler=EODScheduler()
_lock=threading.Lock()
_service=None

def configure(kite_service):
    global _service
    _service=kite_service

def run_full_daily_sync(job_id=None, days=400, delay_seconds=0.42):
    if _service is None: raise RuntimeError('Kite service is not configured')
    if not _lock.acquire(blocking=False):
        if job_id: update_job(job_id,status='skipped',finished_at=datetime.now(timezone.utc).isoformat(),message='Another full-universe sync is already running')
        return
    try:
        _service.require()
        items=universe()
        if job_id is None: job_id=create_job('full_daily_sync',len(items)+2,'Scheduled full NSE 500 refresh')
        update_job(job_id,status='running',total=len(items)+2,message='Refreshing benchmark and NSE 500 daily candles')
        errors=[]; completed=0
        # Refresh both benchmark series first so RS calculations have current reference data.
        for benchmark in ('NIFTY 50','NIFTY 500'):
            try:_service.history(benchmark,'day',days)
            except Exception as exc:errors.append({'symbol':benchmark,'error':str(exc)})
            completed+=1; update_job(job_id,completed=completed,errors=errors)
            time.sleep(delay_seconds)
        for item in items:
            symbol=item['symbol']
            try:_service.history(symbol,'day',days)
            except Exception as exc:errors.append({'symbol':symbol,'error':str(exc)})
            completed+=1
            if completed%10==0 or completed==len(items)+2:
                update_job(job_id,completed=completed,errors=errors,message=f'Refreshed {max(0,completed-2)} of {len(items)} constituents')
            time.sleep(delay_seconds)
        report=daily_report(); report['signal_tracking']=record_and_update(report.get('intelligence',{}).get('opportunities',[])); save_report(report)
        update_job(job_id,status='completed' if not errors else 'completed_with_errors',completed=completed,errors=errors,finished_at=datetime.now(timezone.utc).isoformat(),message=f"Report saved; {len(items)-sum(1 for e in errors if e['symbol'] not in ('NIFTY 50','NIFTY 500'))} constituent histories refreshed")
    except Exception as exc:
        if job_id: update_job(job_id,status='failed',errors=[{'error':str(exc)}],finished_at=datetime.now(timezone.utc).isoformat(),message=str(exc))
        raise
    finally:_lock.release()

def start_full_sync():
    if latest_job() and latest_job().get('status') in ('queued','running'):
        raise RuntimeError('A market-data job is already queued or running')
    count=len(universe())+2
    job_id=create_job('full_daily_sync',count,'Manual full-universe refresh queued')
    threading.Thread(target=run_full_daily_sync,args=(job_id,),daemon=True).start()
    return job_id

def start_scheduler():
    if scheduler.running:return
    # Weekdays after NSE cash-market close. The job skips safely if Kite is not authenticated.
    scheduler.start()

def _scheduled_run():
    try:
        if _service is None or not _service.connected:return
        start_full_sync()
    except Exception:
        pass
