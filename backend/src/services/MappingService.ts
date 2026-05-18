import { db } from '../db/database.js';
import { FieldMapping, SalesforceOrder } from '../types/index.js';

export class MappingService {
  getMappings(): FieldMapping[] {
    return db.prepare('SELECT * FROM field_mapping ORDER BY id').all() as FieldMapping[];
  }

  updateMappings(mappings: FieldMapping[]) {
    const stmt = db.prepare(`INSERT INTO field_mapping (salesforce_field, feishu_field, field_type, enabled) VALUES (?, ?, ?, ?)
      ON CONFLICT(salesforce_field) DO UPDATE SET feishu_field=excluded.feishu_field, field_type=excluded.field_type, enabled=excluded.enabled`);
    db.transaction(() => mappings.forEach((m) => stmt.run(m.salesforce_field, m.feishu_field, m.field_type, m.enabled ? 1 : 0)))();
  }

  toFeishuFields(order: SalesforceOrder, salesforceBaseUrl: string, status: string, syncedAt: string): Record<string, unknown> {
    const special: Record<string, unknown> = {
      SalesforceLink: `${salesforceBaseUrl}/${order.Id}`,
      RawJson: JSON.stringify(order),
      SyncStatus: status,
      LastSyncedAt: syncedAt
    };
    const get = (path: string): unknown => path.split('.').reduce<unknown>((acc, key) => {
      if (acc && typeof acc === 'object') return (acc as Record<string, unknown>)[key];
      return undefined;
    }, order as unknown) ?? special[path];

    return this.getMappings().filter((m) => m.enabled).reduce<Record<string, unknown>>((fields, mapping) => {
      const value = get(mapping.salesforce_field);
      if (value !== undefined && value !== null) fields[mapping.feishu_field] = value;
      return fields;
    }, {});
  }
}
