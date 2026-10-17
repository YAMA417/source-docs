import { index, integer, pgEnum, pgTable, serial, text, timestamp, uniqueIndex } from "drizzle-orm/pg-core";

export const orderStatus = pgEnum("order_status", ["pending", "paid", "cancelled"]);

export const products = pgTable("products", {
  id: serial("id").primaryKey(),
  sku: text("sku").notNull().unique(),
  name: text("name").notNull(),
  stock: integer("stock").notNull().default(0),
});

export const orders = pgTable(
  "orders",
  {
    id: serial("id").primaryKey(),
    productId: integer("product_id").notNull().references(() => products.id),
    status: orderStatus("status").notNull().default("pending"),
    paymentIntentId: text("payment_intent_id"),
    createdAt: timestamp("created_at").defaultNow(),
  },
  (t) => ({
    statusIdx: index("orders_status_idx").on(t.status),
    paymentIdx: uniqueIndex("orders_payment_intent_idx").on(t.paymentIntentId),
  }),
);
