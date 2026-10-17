export default {
  async afterCreate(event) {
    await strapi.db.query("api::author.author").findOne({ where: { id: event.result.author } });
  },
};
