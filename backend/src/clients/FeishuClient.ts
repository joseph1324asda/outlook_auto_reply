import axios, { AxiosInstance } from 'axios';
import { env } from '../config/env.js';
import { ConfigService } from '../services/ConfigService.js';

export class FeishuClient {
  private token?: string;
  private expiresAt = 0;
  private config = new ConfigService();
  private getConfig(key: string, fallback: string) { return this.config.get(key, fallback); }

  async getTenantAccessToken(): Promise<string> {
    if (this.token && Date.now() < this.expiresAt) return this.token;
    const { data } = await axios.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', {
      app_id: this.getConfig('FEISHU_APP_ID', env.feishu.appId),
      app_secret: this.getConfig('FEISHU_APP_SECRET', env.feishu.appSecret)
    });
    if (data.code !== 0) throw new Error(`Feishu token error: ${data.msg}`);
    this.token = data.tenant_access_token;
    this.expiresAt = Date.now() + Math.max(1, data.expire - 300) * 1000;
    return this.token!;
  }

  async http(): Promise<AxiosInstance> {
    const token = await this.getTenantAccessToken();
    return axios.create({ baseURL: 'https://open.feishu.cn/open-apis', headers: { Authorization: `Bearer ${token}` } });
  }

  async testConnection() {
    const http = await this.http();
    const appToken = this.getConfig('FEISHU_BITABLE_APP_TOKEN', env.feishu.bitableAppToken);
    const tableId = this.getConfig('FEISHU_BITABLE_TABLE_ID', env.feishu.bitableTableId);
    const { data } = await http.get(`/bitable/v1/apps/${appToken}/tables/${tableId}`);
    if (data.code !== 0) throw new Error(`Feishu bitable error: ${data.msg}`);
    return { ok: true, table: data.data?.table };
  }

  appToken() { return this.getConfig('FEISHU_BITABLE_APP_TOKEN', env.feishu.bitableAppToken); }
  tableId() { return this.getConfig('FEISHU_BITABLE_TABLE_ID', env.feishu.bitableTableId); }
}
