import axios from 'axios';
export const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL ?? '' });
export type SyncState = { id:number; salesforce_order_id:string; salesforce_order_number?:string; bitable_record_id?:string; last_salesforce_modified_at?:string; last_synced_at?:string; sync_status:string; error_message?:string; raw_json?:string };
export type SyncLog = { id:number; sync_type:string; started_at:string; ended_at?:string; success_count:number; failed_count:number; status:string; error_message?:string };
export type FieldMapping = { id?:number; salesforce_field:string; feishu_field:string; field_type:string; enabled:number };
