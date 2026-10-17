import Link from "next/link";

export default async function ProductsPage() {
  const res = await fetch("/api/products");
  const products = await res.json();
  return (
    <main>
      <ul>{products.map((p: { id: number; name: string }) => <li key={p.id}>{p.name}</li>)}</ul>
      <Link href="/checkout">購入へ</Link>
    </main>
  );
}
