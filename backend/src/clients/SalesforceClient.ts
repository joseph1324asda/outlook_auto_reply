import axios, { AxiosInstance } from 'axios';
import jwt from 'jsonwebtoken';
import fs from 'node:fs';
import { env } from '../config/env.js';
import { ConfigService } from '../services/ConfigService.js';
import { SalesforceOrder } from '../types/index.js';

const ORDER_FIELDS = ['Id','OrderNumber','Status','AccountId','Account.Name','EffectiveDate','EndDate','TotalAmount','ActivatedDate','CreatedDate','LastModifiedDate','OwnerId','Description'];

export class SalesforceClient {
  private accessToken?: string;
  private instanceUrl?: string;
  private expiresAt = 0;
  private config = new ConfigService();

  private getConfig(key: string, fallback: string) { return this.config.get(key, fallback); }

  async authenticate(): Promise<{ instanceUrl: string }> {
    if (this.accessToken && Date.now() < this.expiresAt) return { instanceUrl: this.instanceUrl! };
    const loginUrl = this.getConfig('SALESFORCE_LOGIN_URL', env.salesforce.loginUrl).replace(/\/$/, '');
    const clientId = this.getConfig('SALESFORCE_CLIENT_ID', env.salesforce.clientId);
    const username = this.getConfig('SALESFORCE_USERNAME', env.salesforce.username);
    const privateKeyPath = this.getConfig('SALESFORCE_PRIVATE_KEY_PATH', env.salesforce.privateKeyPath);
    const refreshToken = this.getConfig('SALESFORCE_REFRESH_TOKEN', env.salesforce.refreshToken);
    const clientSecret = this.getConfig('SALESFORCE_CLIENT_SECRET', env.salesforce.clientSecret);

    const body = new URLSearchParams();
    if (refreshToken) {
      body.set('grant_type', 'refresh_token');
      body.set('client_id', clientId);
      if (clientSecret) body.set('client_secret', clientSecret);
      body.set('refresh_token', refreshToken);
    } else {
      const privateKey = fs.readFileSync(privateKeyPath, 'utf8');
      const assertion = jwt.sign({ iss: clientId, sub: username, aud: loginUrl, exp: Math.floor(Date.now() / 1000) + 180 }, privateKey, { algorithm: 'RS256' });
      body.set('grant_type', 'urn:ietf:params:oauth:grant-type:jwt-bearer');
      body.set('assertion', assertion);
    }

    const { data } = await axios.post(`${loginUrl}/services/oauth2/token`, body, { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
    this.accessToken = data.access_token;
    this.instanceUrl = data.instance_url;
    this.expiresAt = Date.now() + 50 * 60 * 1000;
    return { instanceUrl: this.instanceUrl! };
  }

  private async http(): Promise<AxiosInstance> {
    await this.authenticate();
    return axios.create({ baseURL: `${this.instanceUrl}/services/data/${this.getConfig('SALESFORCE_API_VERSION', env.salesforce.apiVersion)}`, headers: { Authorization: `Bearer ${this.accessToken}` } });
  }

  async testConnection() {
    const http = await this.http();
    const { data } = await http.get('/sobjects/Order/describe');
    return { ok: true, name: data.name, label: data.label, instanceUrl: this.instanceUrl };
  }

  buildOrderQuery(lastModifiedAfter?: string, orderId?: string): string {
    const where: string[] = [];
    if (lastModifiedAfter) where.push(`LastModifiedDate >= ${lastModifiedAfter}`);
    if (orderId) where.push(`Id = '${orderId.replace(/'/g, "\\'")}'`);
    return `SELECT ${ORDER_FIELDS.join(', ')} FROM Order${where.length ? ` WHERE ${where.join(' AND ')}` : ''} ORDER BY LastModifiedDate ASC`;
  }

  async queryOrders(lastModifiedAfter?: string, orderId?: string): Promise<SalesforceOrder[]> {
    const http = await this.http();
    const orders: SalesforceOrder[] = [];
    let { data } = await http.get('/query', { params: { q: this.buildOrderQuery(lastModifiedAfter, orderId) } });
    orders.push(...data.records);
    while (!data.done && data.nextRecordsUrl) {
      ({ data } = await http.get(data.nextRecordsUrl.replace(`/services/data/${this.getConfig('SALESFORCE_API_VERSION', env.salesforce.apiVersion)}`, '')));
      orders.push(...data.records);
    }
    return orders;
  }

  getInstanceUrl() { return this.instanceUrl ?? this.getConfig('SALESFORCE_LOGIN_URL', env.salesforce.loginUrl); }
}
