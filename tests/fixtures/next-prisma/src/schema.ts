import { pgTable, text, uuid } from "drizzle-orm/pg-core";

export const books = pgTable("books", {
  id: uuid("id").primaryKey(),
  title: text("title").notNull(),
});
