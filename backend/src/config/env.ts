import dotenv from 'dotenv';
dotenv.config();

export const env = {
  port: Number(process.env.PORT ?? 3000),
  databasePath: process.env.DATABASE_PATH ?? './data/order_sync.sqlite',
  syncCron: process.env.SYNC_CRON ?? '*/10 * * * *',
  syncWindowBackoffMinutes: Number(process.env.SYNC_WINDOW_BACKOFF_MINUTES ?? 5),
  frontendOrigin: process.env.FRONTEND_ORIGIN ?? 'http://localhost:5173',
  salesforce: {
    clientId: process.env.SALESFORCE_CLIENT_ID ?? '',
    username: process.env.SALESFORCE_USERNAME ?? '',
    privateKeyPath: process.env.SALESFORCE_PRIVATE_KEY_PATH ?? '',
    loginUrl: process.env.SALESFORCE_LOGIN_URL ?? 'https://login.salesforce.com',
    apiVersion: process.env.SALESFORCE_API_VERSION ?? 'v60.0',
    refreshToken: process.env.SALESFORCE_REFRESH_TOKEN ?? '',
    clientSecret: process.env.SALESFORCE_CLIENT_SECRET ?? ''
  },
  feishu: {
    appId: process.env.FEISHU_APP_ID ?? '',
    appSecret: process.env.FEISHU_APP_SECRET ?? '',
    bitableAppToken: process.env.FEISHU_BITABLE_APP_TOKEN ?? '',
    bitableTableId: process.env.FEISHU_BITABLE_TABLE_ID ?? ''
  }
};
