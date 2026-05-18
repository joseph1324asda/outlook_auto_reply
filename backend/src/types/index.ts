export type SyncStatus = 'success' | 'failed' | 'skipped' | 'pending';
export type SyncType = 'full' | 'incremental' | 'single' | 'retry';

export interface SalesforceOrder {
  Id: string;
  OrderNumber?: string;
  Status?: string;
  AccountId?: string;
  Account?: { Name?: string } | null;
  EffectiveDate?: string;
  EndDate?: string;
  TotalAmount?: number;
  ActivatedDate?: string;
  CreatedDate?: string;
  LastModifiedDate?: string;
  OwnerId?: string;
  Description?: string;
  attributes?: { url?: string };
}

export interface FieldMapping {
  id?: number;
  salesforce_field: string;
  feishu_field: string;
  field_type: string;
  enabled: number;
}

export interface AppConfig {
  key: string;
  value: string;
  secret: number;
}

export interface SyncState {
  id?: number;
  salesforce_order_id: string;
  salesforce_order_number?: string | null;
  bitable_record_id?: string | null;
  last_hash?: string | null;
  last_salesforce_modified_at?: string | null;
  last_synced_at?: string | null;
  sync_status: SyncStatus;
  error_message?: string | null;
  raw_json?: string | null;
  created_at?: string;
  updated_at?: string;
}
