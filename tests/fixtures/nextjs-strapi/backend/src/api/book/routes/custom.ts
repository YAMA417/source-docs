export default {
  routes: [
    { method: "POST", path: "/books/:id/archive", handler: "book.archive" },
  ],
};
