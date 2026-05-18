import { Router } from 'express';
import { ConfigService } from '../services/ConfigService.js';
export function configController() {
  const router = Router();
  const service = new ConfigService();
  router.get('/config', (_req, res) => res.json(service.getAll(true)));
  router.put('/config', (req, res) => { service.upsertMany(req.body.items ?? []); res.json({ ok: true }); });
  return router;
}
