export default async function Home() {
  const res = await fetch(
    "https://api.github.com/repos/acme/app",
    {
      headers: { Authorization: `Bearer ${process.env.GITHUB_ACCESS_TOKEN}` },
    },
  );
  return null;
}
