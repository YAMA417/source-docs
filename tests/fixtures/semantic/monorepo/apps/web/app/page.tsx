export default async function Home() {
  const res = await fetch("/api/items");
  const health = await fetch("/health");
  const upstream = await fetch(`${process.env.API_URL}/health`);
  return null;
}
