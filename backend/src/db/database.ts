import Database from 'better-sqlite3';
import fs from 'node:fs';
import path from 'node:path';
import { env } from '../config/env.js';

fs.mkdirSync(path.dirname(env.databasePath), { recursive: true });
export const db = new Database(env.databasePath);
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

export function migrate() {
  db.exec(`
CREATE TABLE IF NOT EXISTS order_sync_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  salesforce_order_id TEXT NOT NULL UNIQUE,
  salesforce_order_number TEXT,
  bitable_record_id TEXT,
  last_hash TEXT,
  last_salesforce_modified_at TEXT,
  last_synced_at TEXT,
  sync_status TEXT NOT NULL DEFAULT 'pending',
  error_message TEXT,
  raw_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_order_sync_state_modified ON order_sync_state(last_salesforce_modified_at);
CREATE INDEX IF NOT EXISTS idx_order_sync_state_status ON order_sync_state(sync_status);

CREATE TABLE IF NOT EXISTS sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sync_type TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  success_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  error_message TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS field_mapping (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  salesforce_field TEXT NOT NULL UNIQUE,
  feishu_field TEXT NOT NULL,
  field_type TEXT NOT NULL DEFAULT 'text',
  enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS app_config (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '',
  secret INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
`);

  const count = db.prepare('SELECT COUNT(*) as c FROM field_mapping').get() as { c: number };
  if (count.c === 0) {
    const mappings = [
      ['Id', 'Salesforce订单ID', 'text'], ['OrderNumber', '订单编号', 'text'], ['Status', '订单状态', 'text'],
      ['Account.Name', '客户名称', 'text'], ['AccountId', '客户ID', 'text'], ['TotalAmount', '订单金额', 'number'],
      ['EffectiveDate', '生效日期', 'date'], ['EndDate', '结束日期', 'date'], ['ActivatedDate', '激活日期', 'date'],
      ['CreatedDate', 'Salesforce创建时间', 'date'], ['LastModifiedDate', 'Salesforce更新时间', 'date'], ['OwnerId', '负责人', 'text'],
      ['Description', '描述', 'text'], ['SalesforceLink', 'Salesforce链接', 'url'], ['RawJson', '原始JSON', 'text'],
      ['SyncStatus', '同步状态', 'text'], ['LastSyncedAt', '最后同步时间', 'date']
    ];
    const stmt = db.prepare('INSERT INTO field_mapping (salesforce_field, feishu_field, field_type) VALUES (?, ?, ?)');
    const tx = db.transaction(() => mappings.forEach((m) => stmt.run(...m)));
    tx();
  }
}
