export default async function BooksPage() {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_BASE}/api/books`);
  const books = await res.json();
  return <ul>{books.data.map((b: { id: number; title: string }) => <li key={b.id}><a href={`/books/${b.id}`}>{b.title}</a></li>)}</ul>;
}
