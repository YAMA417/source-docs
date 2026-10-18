export default async function Admin() {
  await fetch("/orders", { method: "POST" });
  return null;
}
