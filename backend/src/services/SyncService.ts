import { SalesforceClient } from '../clients/SalesforceClient.js';
import { BitableService } from './BitableService.js';
import { MappingService } from './MappingService.js';
import { SyncLogService } from './SyncLogService.js';
import { db } from '../db/database.js';
import { SalesforceOrder, SyncState, SyncType } from '../types/index.js';
import { env } from '../config/env.js';
import { errorMessage, withRetry } from '../utils/retry.js';
import { hashObject } from '../utils/hash.js';

export class SyncService {
  constructor(
    private sf = new SalesforceClient(),
    private bitable = new BitableService(),
    private mapping = new MappingService(),
    private logs = new SyncLogService()
  ) {}

  latestModified(): string | undefined {
    const row = db.prepare("SELECT MAX(last_salesforce_modified_at) as value FROM order_sync_state WHERE sync_status != 'failed'").get() as { value?: string };
    if (!row.value) return undefined;
    return new Date(new Date(row.value).getTime() - env.syncWindowBackoffMinutes * 60_000).toISOString();
  }

  async syncFull() { return this.run('full'); }
  async syncIncremental() { return this.run('incremental', this.latestModified()); }
  async syncOrder(salesforceOrderId: string) { return this.run('single', undefined, salesforceOrderId); }
  async retryFailed() {
    const failed = db.prepare("SELECT salesforce_order_id FROM order_sync_state WHERE sync_status = 'failed' ORDER BY updated_at DESC LIMIT 50").all() as { salesforce_order_id: string }[];
    const logId = this.logs.start('retry');
    let success = 0, failedCount = 0;
    for (const row of failed) {
      try { const result = await this.run('single', undefined, row.salesforce_order_id, false); success += result.successCount; failedCount += result.failedCount; }
      catch { failedCount += 1; }
    }
    this.logs.finish(logId, success, failedCount, failedCount ? 'partial_failed' : 'success');
    return { successCount: success, failedCount };
  }

  private async run(syncType: SyncType, lastModifiedAfter?: string, orderId?: string, createLog = true) {
    const logId = createLog ? this.logs.start(syncType) : 0;
    let successCount = 0, failedCount = 0;
    try {
      const orders = await withRetry(() => this.sf.queryOrders(lastModifiedAfter, orderId));
      for (const order of orders) {
        try {
          const changed = await this.syncOne(order);
          if (changed) successCount += 1;
        } catch (error) {
          failedCount += 1;
          this.recordError(order, errorMessage(error));
        }
      }
      if (createLog) this.logs.finish(logId, successCount, failedCount, failedCount ? 'partial_failed' : 'success');
      return { total: orders.length, successCount, failedCount };
    } catch (error) {
      const message = errorMessage(error);
      if (createLog) this.logs.finish(logId, successCount, failedCount + 1, 'failed', message);
      throw error;
    }
  }

  private async syncOne(order: SalesforceOrder): Promise<boolean> {
    const existing = db.prepare('SELECT * FROM order_sync_state WHERE salesforce_order_id = ?').get(order.Id) as SyncState | undefined;
    const payloadHash = hashObject(order);
    const syncedAt = new Date().toISOString();
    if (existing?.last_hash === payloadHash && existing.bitable_record_id) {
      this.upsertState(order, existing.bitable_record_id, payloadHash, 'skipped', null, syncedAt);
      return false;
    }

    const fields = this.mapping.toFeishuFields(order, this.sf.getInstanceUrl(), 'success', syncedAt);
    let recordId = existing?.bitable_record_id ?? null;
    if (recordId) await withRetry(() => this.bitable.updateRecord(recordId!, fields));
    else recordId = await withRetry(() => this.bitable.createRecord(fields));
    this.upsertState(order, recordId, payloadHash, 'success', null, syncedAt);
    return true;
  }

  private upsertState(order: SalesforceOrder, recordId: string | null, hash: string | null, status: string, error: string | null, syncedAt?: string) {
    db.prepare(`INSERT INTO order_sync_state
      (salesforce_order_id, salesforce_order_number, bitable_record_id, last_hash, last_salesforce_modified_at, last_synced_at, sync_status, error_message, raw_json, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
      ON CONFLICT(salesforce_order_id) DO UPDATE SET salesforce_order_number=excluded.salesforce_order_number, bitable_record_id=COALESCE(excluded.bitable_record_id, order_sync_state.bitable_record_id), last_hash=excluded.last_hash, last_salesforce_modified_at=excluded.last_salesforce_modified_at, last_synced_at=excluded.last_synced_at, sync_status=excluded.sync_status, error_message=excluded.error_message, raw_json=excluded.raw_json, updated_at=CURRENT_TIMESTAMP`)
      .run(order.Id, order.OrderNumber ?? null, recordId, hash, order.LastModifiedDate ?? null, syncedAt ?? new Date().toISOString(), status, error, JSON.stringify(order));
  }

  private recordError(order: SalesforceOrder, message: string) {
    this.upsertState(order, null, null, 'failed', message, new Date().toISOString());
  }

  listOrders(limit = 100, offset = 0) {
    return db.prepare('SELECT * FROM order_sync_state ORDER BY last_salesforce_modified_at DESC LIMIT ? OFFSET ?').all(limit, offset);
  }

  status() {
    const totals = db.prepare(`SELECT COUNT(*) total, SUM(sync_status='failed') failed FROM order_sync_state`).get() as { total: number; failed: number };
    const today = db.prepare(`SELECT SUM(sync_status='success' AND date(last_synced_at)=date('now')) successToday, SUM(sync_status='skipped' AND date(last_synced_at)=date('now')) skippedToday FROM order_sync_state`).get();
    const recent = db.prepare('SELECT MAX(last_synced_at) recentSyncAt FROM order_sync_state').get();
    return { ...totals, ...(today as object), ...(recent as object) };
  }
}
