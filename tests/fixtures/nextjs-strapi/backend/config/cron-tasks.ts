export default {
  publishScheduled: {
    task: async ({ strapi }) => {
      await strapi.db.query("api::book.book").updateMany({ where: {}, data: {} });
    },
    options: { rule: "0 * * * *" },
  },
};
