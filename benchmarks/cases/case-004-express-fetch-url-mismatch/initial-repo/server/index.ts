import express from "express";
import { clientsRouter } from "./clients-router";

const app = express();
app.use("/invoices", clientsRouter);
app.listen(3000);
