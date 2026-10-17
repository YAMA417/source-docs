import { Worker } from "bullmq";
import { eq } from "drizzle-orm";
import { db } from "@/lib/db";
import { orders } from "@/db/schema";

export const receiptWorker = new Worker("receipts", async (job) => {
  const rows = await db.select().from(orders).where(eq(orders.id, job.data.orderId));
  return rows.length;
});
