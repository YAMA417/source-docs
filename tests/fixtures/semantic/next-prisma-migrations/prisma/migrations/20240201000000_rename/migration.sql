-- テーブル名とカラム名を変え、FK を張り直す
ALTER TABLE "articles" RENAME TO "posts";
ALTER TABLE "posts" RENAME COLUMN "user_id" TO "author_id";
ALTER TABLE "posts" DROP CONSTRAINT "articles_user_id_fkey";
ALTER TABLE "posts" ADD CONSTRAINT "posts_author_id_fkey" FOREIGN KEY ("author_id") REFERENCES "users"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "posts" ALTER COLUMN "title" TYPE TEXT;
ALTER TABLE "posts" ADD COLUMN "published" BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN "draft_note" TEXT;
