import { Hono } from "hono";
import { eq } from "drizzle-orm";
import { db } from "../../db/client";
import { books as booksTable, loans } from "../../db/schema";

const books = new Hono();

books.get("/", async (c) => c.json(await db.select().from(booksTable)));

books.post("/", async (c) => {
  const body = await c.req.json();
  return c.json(await db.insert(booksTable).values(body).returning(), 201);
});

books.post("/:id/loans", async (c) => {
  await db.insert(loans).values({ bookId: Number(c.req.param("id")) });
  await db.update(booksTable).set({ available: false }).where(eq(booksTable.id, Number(c.req.param("id"))));
  return c.body(null, 204);
});

export default books;
