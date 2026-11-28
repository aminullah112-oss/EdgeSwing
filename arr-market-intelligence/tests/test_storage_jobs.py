import backend.db as db

def test_job_state_and_report_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATABASE_PATH', str(tmp_path / 'test.sqlite'))
    db.init_db()
    job_id = db.create_job('full_daily_sync', 502, 'queued')
    db.update_job(job_id, status='running', completed=12, errors=[{'symbol':'ABC','error':'sample'}])
    current = db.latest_job()
    assert current['status'] == 'running'
    assert current['completed'] == 12
    assert current['errors'][0]['symbol'] == 'ABC'
    report = {'generated_at':'2026-11-28T12:00:00+00:00','analysed':0,'categories':{}}
    db.save_report(report)
    history = db.report_history()
    assert len(history) == 1
    assert db.get_report(history[0]['id'])['generated_at'] == report['generated_at']

def test_sector_migration_and_signal_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATABASE_PATH', str(tmp_path / 'signals.sqlite'))
    db.init_db()
    db.save_universe([('ABC', 'ABC Industries', 'Industrials')])
    assert db.universe()[0]['sector'] == 'Industrials'
    opportunity = {
        'symbol': 'ABC', 'signal_date': '2026-11-27', 'date': '2026-11-27',
        'close': 100.0, 'entry_trigger': 101.0, 'stop_loss': 98.0,
        'target_price': 107.0, 'confidence': 82.5,
    }
    assert db.save_signals([opportunity]) == 1
    assert db.save_signals([opportunity]) == 0
    active = db.active_signals()
    assert len(active) == 1 and active[0]['status'] == 'active'
    db.update_signal_outcome(active[0]['id'], 'target_hit', '2026-11-28', 107.0, 5.94)
    assert db.signal_history()[0]['status'] == 'target_hit'

def test_signal_tracker_waits_for_trigger_and_assumes_stop_first(tmp_path, monkeypatch):
    import backend.signals as signals
    monkeypatch.setattr(db, 'DATABASE_PATH', str(tmp_path / 'tracker.sqlite'))
    db.init_db()
    db.save_signals([{
        'symbol':'XYZ', 'signal_date':'2026-01-01', 'date':'2026-01-01',
        'close':100, 'entry_trigger':101, 'stop_loss':99, 'target_price':105,
        'confidence':80,
    }])
    db.save_candles('XYZ', 'day', [
        {'date':'2026-01-02', 'open':100, 'high':100.5, 'low':99.5, 'close':100.2, 'volume':1000},
        {'date':'2026-01-05', 'open':100.5, 'high':101.5, 'low':100.2, 'close':101.2, 'volume':1200},
        {'date':'2026-01-06', 'open':101.2, 'high':106, 'low':98, 'close':102, 'volume':1500},
    ])
    result = signals.record_and_update([])
    assert result['resolved_signals'] == 1
    row = db.signal_history()[0]
    assert row['status'] == 'stop_hit'
    assert row['actual_entry'] == 101.0
    assert row['exit_price'] == 99.0
    assert row['pnl_pct'] < 0
