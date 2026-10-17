import { factories } from "@strapi/strapi";

export default factories.createCoreController("api::book.book", ({ strapi }) => ({
  async archive(ctx) {
    const { id } = ctx.params;
    return strapi.documents("api::book.book").update({ documentId: id, data: { title: "archived" } });
  },
}));
