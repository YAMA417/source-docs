import { stripe } from "@/lib/stripe";

export async function POST(req: Request) {
  const signature = req.headers.get("stripe-signature")!;
  const event = stripe.webhooks.constructEvent(
    await req.text(), signature, process.env.STRIPE_WEBHOOK_SECRET!);
  return new Response(null, { status: 200 });
}
