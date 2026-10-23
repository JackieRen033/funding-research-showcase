"""Append-only observations; event labels never overwrite historical snapshots."""
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from .schema import FundingEvent


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS snapshots (
          id TEXT PRIMARY KEY, exchange TEXT, symbol TEXT, observed_ms INTEGER, payload TEXT);
        CREATE INDEX IF NOT EXISTS snapshot_lookup ON snapshots(exchange,symbol,observed_ms);
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, exchange TEXT, symbol TEXT, funding_ms INTEGER, first_seen_ms INTEGER);
        CREATE INDEX IF NOT EXISTS event_lookup ON events(exchange,symbol,funding_ms);
        CREATE TABLE IF NOT EXISTS event_observations (
          event_id TEXT REFERENCES events(id), snapshot_id TEXT REFERENCES snapshots(id),
          observed_ms INTEGER, payload TEXT, PRIMARY KEY(event_id,snapshot_id));
        CREATE TABLE IF NOT EXISTS event_snapshots (
          event_id TEXT REFERENCES events(id), snapshot_id TEXT REFERENCES snapshots(id),
          offset_ms INTEGER, phase TEXT, PRIMARY KEY(event_id,snapshot_id));
        ''')

    def save(self, s, pre_seconds, post_seconds):
        with self.db:
            self.db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?)',
                (s.snapshot_id, s.exchange, s.symbol, s.collected_at_ms,
                 json.dumps(s.to_dict(), ensure_ascii=False, allow_nan=False)))
            events = list(s.history)
            if s.next_funding_time_ms:
                events.append(FundingEvent(s.exchange, s.symbol, s.next_funding_time_ms,
                                           observed_at_ms=s.collected_at_ms))
            for e in events:
                self.db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',
                    (e.event_id, e.exchange, e.symbol, e.funding_time_ms, e.observed_at_ms))
                self.db.execute('INSERT OR IGNORE INTO event_observations VALUES (?,?,?,?)',
                    (e.event_id, s.snapshot_id, e.observed_at_ms, json.dumps(asdict(e))))
                # Link earlier captured snapshots when an event is first discovered after restart.
                # Linkage is retrospective metadata, not evidence the schedule was known earlier.
                rows = self.db.execute('SELECT id,observed_ms FROM snapshots WHERE exchange=? AND symbol=? '
                    'AND observed_ms BETWEEN ? AND ?', (s.exchange, s.symbol,
                    e.funding_time_ms - pre_seconds * 1000, e.funding_time_ms + post_seconds * 1000))
                for sid, observed in rows:
                    self._link(e.event_id, sid, observed - e.funding_time_ms)
            # Persisted schedule survives rollover, missing history and process restart.
            for eid, t in self.db.execute('SELECT id,funding_ms FROM events WHERE exchange=? AND symbol=? '
                    'AND funding_ms BETWEEN ? AND ?', (s.exchange, s.symbol,
                    s.collected_at_ms - post_seconds * 1000, s.collected_at_ms + pre_seconds * 1000)):
                self._link(eid, s.snapshot_id, s.collected_at_ms - t)

    def _link(self, event_id, snapshot_id, offset):
        self.db.execute('INSERT OR IGNORE INTO event_snapshots VALUES (?,?,?,?)',
                        (event_id, snapshot_id, offset, 'pre' if offset < 0 else 'post'))

    def export(self, stream, event_id=None):
        if event_id:
            rows = self.db.execute('SELECT s.payload FROM snapshots s JOIN event_snapshots es '
                'ON es.snapshot_id=s.id WHERE es.event_id=? ORDER BY s.observed_ms', (event_id,))
        else:
            rows = self.db.execute('SELECT payload FROM snapshots ORDER BY observed_ms')
        for row in rows:
            stream.write(row[0] + '\n')

    def near_event(self, now, pre_seconds, post_seconds):
        return self.db.execute('SELECT 1 FROM events WHERE funding_ms BETWEEN ? AND ? LIMIT 1',
             (now - post_seconds * 1000, now + pre_seconds * 1000)).fetchone() is not None

    def close(self):
        self.db.close()
