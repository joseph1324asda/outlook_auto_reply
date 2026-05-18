import { FeishuClient } from '../clients/FeishuClient.js';

export class BitableService {
  constructor(private feishu = new FeishuClient()) {}

  async createRecord(fields: Record<string, unknown>): Promise<string> {
    const http = await this.feishu.http();
    const { data } = await http.post(`/bitable/v1/apps/${this.feishu.appToken()}/tables/${this.feishu.tableId()}/records`, { fields });
    if (data.code !== 0) throw new Error(`Create bitable record failed: ${data.msg}`);
    return data.data.record.record_id;
  }

  async updateRecord(recordId: string, fields: Record<string, unknown>): Promise<void> {
    const http = await this.feishu.http();
    const { data } = await http.put(`/bitable/v1/apps/${this.feishu.appToken()}/tables/${this.feishu.tableId()}/records/${recordId}`, { fields });
    if (data.code !== 0) throw new Error(`Update bitable record failed: ${data.msg}`);
  }

  async batchUpdate(records: Array<{ record_id: string; fields: Record<string, unknown> }>): Promise<void> {
    if (records.length === 0) return;
    const http = await this.feishu.http();
    const { data } = await http.post(`/bitable/v1/apps/${this.feishu.appToken()}/tables/${this.feishu.tableId()}/records/batch_update`, { records });
    if (data.code !== 0) throw new Error(`Batch update bitable records failed: ${data.msg}`);
  }
}
