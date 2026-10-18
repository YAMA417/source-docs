ALTER TABLE "users" DROP COLUMN "legacy_flag";
ALTER TABLE "users" ALTER COLUMN "email" SET NOT NULL;
CREATE UNIQUE INDEX "users_email_key" ON "users"("email");
CREATE UNIQUE INDEX "posts_author_id_title_key" ON "posts"("author_id", "title");
ALTER TABLE "posts" DROP COLUMN "draft_note";
ALTER TABLE "posts" ALTER COLUMN "published" DROP DEFAULT;
