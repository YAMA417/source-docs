import { db } from "@/lib/db";
import { stripe } from "@/lib/stripe";
import { receiptQueue } from "@/lib/queue";
import { orders } from "@/db/schema";

export async function createOrder(productId: number) {
  const intent = await stripe.paymentIntents.create({ amount: 1000, currency: "jpy" });
  const [order] = await db.insert(orders).values({ productId, paymentIntentId: intent.id }).returning();
  await receiptQueue.add("send-receipt", { orderId: order.id });
  return order;
}
