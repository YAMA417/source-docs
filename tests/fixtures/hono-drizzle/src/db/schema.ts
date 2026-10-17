import { boolean, integer, pgTable, serial, text, check } from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";

export const books = pgTable("books", {
  id: serial("id").primaryKey(),
  title: text("title").notNull(),
  available: boolean("available").notNull().default(true),
});

export const loans = pgTable("loans", {
  id: serial("id").primaryKey(),
  bookId: integer("book_id").notNull().references(() => books.id),
  days: integer("days").notNull().default(14),
}, (t) => [check("loans_days_positive", sql`${t.days} > 0`)]);
