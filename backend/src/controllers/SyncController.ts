import { Router } from 'express';
import { SyncService } from '../services/SyncService.js';
import { SyncLogService } from '../services/SyncLogService.js';
import { errorMessage } from '../utils/retry.js';

export function syncController() {
  const router = Router();
  const service = new SyncService();
  const logs = new SyncLogService();
  router.post('/sync/full', async (_req, res) => { try { res.json(await service.syncFull()); } catch (e) { res.status(500).json({ error: errorMessage(e) }); } });
  router.post('/sync/incremental', async (_req, res) => { try { res.json(await service.syncIncremental()); } catch (e) { res.status(500).json({ error: errorMessage(e) }); } });
  router.post('/sync/order/:salesforceOrderId', async (req, res) => { try { res.json(await service.syncOrder(req.params.salesforceOrderId)); } catch (e) { res.status(500).json({ error: errorMessage(e) }); } });
  router.post('/sync/retry', async (_req, res) => { try { res.json(await service.retryFailed()); } catch (e) { res.status(500).json({ error: errorMessage(e) }); } });
  router.get('/sync/logs', (req, res) => res.json(logs.list(Number(req.query.limit ?? 100))));
  return router;
}
