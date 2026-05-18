import express from 'express';
import cors from 'cors';
import cron from 'node-cron';
import { env } from './config/env.js';
import { migrate } from './db/database.js';
import { apiRoutes } from './routes/index.js';
import { SyncService } from './services/SyncService.js';
import { errorMessage } from './utils/retry.js';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

migrate();
const app = express();
app.use(cors({ origin: env.frontendOrigin, credentials: true }));
app.use(express.json({ limit: '5mb' }));
app.get('/health', (_req, res) => res.json({ ok: true, service: 'salesforce-feishu-order-sync' }));
app.use('/api', apiRoutes());
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendDist = path.resolve(__dirname, '../../frontend/dist');
app.use(express.static(frontendDist));
app.get('*', (req, res, next) => req.path.startsWith('/api') ? next() : res.sendFile(path.join(frontendDist, 'index.html')));

cron.schedule(env.syncCron, async () => {
  try { await new SyncService().syncIncremental(); }
  catch (error) { console.error('Scheduled incremental sync failed:', errorMessage(error)); }
});

app.listen(env.port, () => console.log(`API listening on :${env.port}`));
