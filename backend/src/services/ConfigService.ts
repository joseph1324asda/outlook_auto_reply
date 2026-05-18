import { db } from '../db/database.js';
import { AppConfig } from '../types/index.js';

export class ConfigService {
  getAll(maskSecrets = true): AppConfig[] {
    const rows = db.prepare('SELECT key, value, secret FROM app_config ORDER BY key').all() as AppConfig[];
    return maskSecrets ? rows.map((row) => row.secret ? { ...row, value: row.value ? '********' : '' } : row) : rows;
  }

  get(key: string, fallback = ''): string {
    const row = db.prepare('SELECT value FROM app_config WHERE key = ?').get(key) as { value: string } | undefined;
    return row?.value || process.env[key] || fallback;
  }

  upsertMany(items: AppConfig[]) {
    const stmt = db.prepare(`INSERT INTO app_config (key, value, secret, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)
      ON CONFLICT(key) DO UPDATE SET value=excluded.value, secret=excluded.secret, updated_at=CURRENT_TIMESTAMP`);
    db.transaction(() => items.forEach((item) => stmt.run(item.key, item.value, item.secret ? 1 : 0)))();
  }
}
