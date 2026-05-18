import { db } from '../db/database.js';
import { SyncType } from '../types/index.js';

export class SyncLogService {
  start(syncType: SyncType) {
    const started = new Date().toISOString();
    const info = db.prepare('INSERT INTO sync_log (sync_type, started_at, status) VALUES (?, ?, ?)').run(syncType, started, 'running');
    return Number(info.lastInsertRowid);
  }
  finish(id: number, success: number, failed: number, status: string, error?: string) {
    db.prepare('UPDATE sync_log SET ended_at=?, success_count=?, failed_count=?, status=?, error_message=? WHERE id=?')
      .run(new Date().toISOString(), success, failed, status, error ?? null, id);
  }
  list(limit = 100) {
    return db.prepare('SELECT * FROM sync_log ORDER BY started_at DESC LIMIT ?').all(limit);
  }
}
