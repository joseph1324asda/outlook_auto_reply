import { Router } from 'express';
import { SyncService } from '../services/SyncService.js';
export function orderController() {
  const router = Router();
  const service = new SyncService();
  router.get('/orders', (req, res) => res.json(service.listOrders(Number(req.query.limit ?? 100), Number(req.query.offset ?? 0))));
  return router;
}
