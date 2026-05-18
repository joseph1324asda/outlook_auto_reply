import { Router } from 'express';
import { MappingService } from '../services/MappingService.js';
export function mappingController() {
  const router = Router();
  const service = new MappingService();
  router.get('/mapping', (_req, res) => res.json(service.getMappings()));
  router.put('/mapping', (req, res) => { service.updateMappings(req.body.items ?? []); res.json({ ok: true }); });
  return router;
}
