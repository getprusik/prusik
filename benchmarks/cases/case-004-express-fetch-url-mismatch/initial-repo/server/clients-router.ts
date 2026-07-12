import { Router } from "express";

export const clientsRouter = Router();

clientsRouter.get("/clients/search", (req, res) => {
  const q = req.query.q;
  res.json({ matches: [], query: q });
});
