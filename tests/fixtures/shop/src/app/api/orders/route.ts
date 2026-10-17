import { createOrder } from "@/services/orders";

export async function POST(req: Request) {
  const { productId } = await req.json();
  if (!productId) {
    return Response.json({ error: "productId is required" }, { status: 400 });
  }
  const order = await createOrder(productId);
  return Response.json(order, { status: 201 });
}
