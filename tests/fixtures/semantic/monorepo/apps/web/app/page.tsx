export default async function Home() {
  const res = await fetch("/api/items");
  const health = await fetch("/health");
  return null;
}
