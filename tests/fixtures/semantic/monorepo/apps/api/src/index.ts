import express from "express";

const app = express();
app.get("/health", (req, res) => res.send("ok"));
app.post("/orders", (req, res) => res.json({}));
app.listen(3000);
