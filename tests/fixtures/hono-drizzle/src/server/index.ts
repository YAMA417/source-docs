import { Hono } from "hono";
import books from "./routes/books";

const app = new Hono().basePath("/api");
app.route("/books", books);
app.get("/health", (c) => c.json({ ok: true }));

export default app;
