import { eq } from "drizzle-orm";
import { db } from "@/lib/db";
import { stripe } from "@/lib/stripe";
import { orders } from "@/db/schema";
import { events } from "@/events/bus";

export async function POST(req: Request) {
  const signature = req.headers.get("stripe-signature")!;
  const event = stripe.webhooks.constructEvent(await req.text(), signature, process.env.STRIPE_WEBHOOK_SECRET!);
  if (event.type === "payment_intent.succeeded") {
    await db.update(orders).set({ status: "paid" }).where(eq(orders.paymentIntentId, event.data.object.id));
    events.emit("order.paid", { paymentIntentId: event.data.object.id });
  }
  return new Response(null, { status: 204 });
}
