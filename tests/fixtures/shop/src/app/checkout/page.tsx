"use client";
import { useRouter } from "next/navigation";

export default function CheckoutPage() {
  const router = useRouter();
  async function submit(productId: number) {
    await fetch("/api/orders", { method: "POST", body: JSON.stringify({ productId }) });
    router.push("/products");
  }
  return <button onClick={() => submit(1)}>注文を確定する</button>;
}
