CREATE TABLE "users" (
    "id" TEXT NOT NULL,
    "email" TEXT,
    "name" TEXT,
    "legacy_flag" BOOLEAN NOT NULL DEFAULT false,
    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "articles" (
    "id" TEXT NOT NULL,
    "title" VARCHAR(100) NOT NULL,
    "user_id" TEXT NOT NULL,
    CONSTRAINT "articles_pkey" PRIMARY KEY ("id")
);

ALTER TABLE "articles" ADD CONSTRAINT "articles_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
