import { Router } from 'express';
import { SalesforceClient } from '../clients/SalesforceClient.js';
import { FeishuClient } from '../clients/FeishuClient.js';
import { SyncService } from '../services/SyncService.js';
import { errorMessage } from '../utils/retry.js';
import { syncController } from '../controllers/SyncController.js';
import { orderController } from '../controllers/OrderController.js';
import { configController } from '../controllers/ConfigController.js';
import { mappingController } from '../controllers/MappingController.js';

export function apiRoutes() {
  const router = Router();
  router.get('/status', (_req, res) => res.json(new SyncService().status()));
  router.post('/test/salesforce', async (_req, res) => { try { res.json(await new SalesforceClient().testConnection()); } catch (e) { res.status(500).json({ ok: false, error: errorMessage(e) }); } });
  router.post('/test/feishu', async (_req, res) => { try { res.json(await new FeishuClient().testConnection()); } catch (e) { res.status(500).json({ ok: false, error: errorMessage(e) }); } });
  router.use(syncController(), orderController(), configController(), mappingController());
  return router;
}
